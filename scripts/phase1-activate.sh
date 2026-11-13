#!/usr/bin/env bash
# ClipForge Phase 1 — safe host activation.
# Run on mt-tech-server as the existing non-root owner of ~/ClipForge.
set -Eeuo pipefail

usage() {
  cat <<'HELP'
Usage:
  bash scripts/phase1-activate.sh --prepare  # pull, configure admin, start host agent
  bash scripts/phase1-activate.sh --deploy   # verify idle queue and deploy application
  bash scripts/phase1-activate.sh --verify   # read-only status after deployment
HELP
}
[[ $# -eq 1 ]] || { usage; exit 2; }
action="$1"
case "$action" in --prepare|--deploy|--verify) ;; *) usage; exit 2 ;; esac

[[ "$EUID" != 0 ]] || { echo "Refusing root execution" >&2; exit 1; }
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo"
[[ -f .env && ! -L .env ]] || { echo "Host .env missing or is a symlink" >&2; exit 1; }

check_queue() {
  python3 - <<'PY'
import json
from urllib.request import urlopen

with urlopen("http://127.0.0.1:8765/api/jobs", timeout=10) as response:
    jobs = json.load(response)
if not isinstance(jobs, list):
    raise SystemExit("Unsupported job API response; deployment stopped")
terminal = {"completed", "failed", "cancelled", "canceled"}
active = [(j.get("id"), j.get("status")) for j in jobs
          if not isinstance(j, dict) or j.get("status") not in terminal]
if active:
    for job_id, status in active:
        print("ACTIVE:", job_id, status)
    raise SystemExit("Deployment blocked: queue is not idle")
print("Queue verified idle:", len(jobs), "finished jobs")
PY
}

wait_api_ready() {
  # Docker "Started" means the container process launched, not that Uvicorn
  # finished importing and recovering its in-process job queue.
  local attempt
  for attempt in {1..45}; do
    if curl --silent --fail --max-time 3 \
        http://127.0.0.1:8765/api/health >/dev/null 2>&1; then
      curl --fail --silent --show-error --max-time 10 \
        http://127.0.0.1:8765/api/health
      printf '\n'
      return 0
    fi
    sleep 2
  done
  echo "ClipForge did not become API-ready after container startup." >&2
  echo "No further Docker restarts were attempted; diagnostics follow." >&2
  docker compose --profile ai ps >&2 || true
  docker compose --profile ai logs --tail=60 --no-color clipforge >&2 || true
  return 1
}

case "$action" in
  --prepare)
    [[ "$(git branch --show-current)" == "main" ]] || {
      echo "Switch to Git main on the server before running setup." >&2; exit 1;
    }
    git diff --quiet && git diff --cached --quiet || {
      echo "Tracked local changes present; inspect before updating." >&2; exit 1;
    }
    git pull --ff-only
    if [[ ! -x .venv/bin/python ]]; then python3 -m venv .venv; fi
    .venv/bin/python -m pip install -e .
    chmod 600 .env
    if ! .venv/bin/python -c 'from pathlib import Path; from clipforge.environment import read_env; raise SystemExit(0 if read_env(Path(".env")).get("CLIPFORGE_ADMIN_PASSWORD_HASH", "").startswith("$argon2id$") else 1)'; then
      [[ -t 0 ]] || {
        echo "Administrator password setup requires an interactive terminal." >&2; exit 1;
      }
      .venv/bin/python -m clipforge.host_setup --root "$repo"
    else
      echo "Existing Argon2id admin credential detected; leaving it unchanged."
    fi
    .venv/bin/python -m clipforge.config_agent --root "$repo" --setup
    unit_dir="$HOME/.config/systemd/user"
    mkdir -p "$unit_dir"
    python3 - "$repo" "$unit_dir/clipforge-config-agent.service" <<'PY'
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
unit = pathlib.Path(sys.argv[2])
# Unit WorkingDirectory= requires an UNQUOTED absolute path. Unlike a shell,
# quotes are parsed as literal content here; they trigger systemd bad-setting.
# Fail closed for paths that would need systemd-specific escaping.
if not re.fullmatch(r"/[A-Za-z0-9_./+-]+", str(root)):
    raise SystemExit("Unsupported characters in project path for systemd unit")
unit.write_text(
    "[Unit]\n"
    "Description=ClipForge host configuration agent (restricted Unix socket)\n"
    "[Service]\n"
    "Type=simple\n"
    f"WorkingDirectory={root}\n"
    f"ExecStart={root}/.venv/bin/python -m clipforge.config_agent --root {root}\n"
    "Restart=on-failure\n"
    "RestartSec=3\n"
    "NoNewPrivileges=true\n"
    "PrivateTmp=true\n"
    "UMask=0027\n"
    "[Install]\n"
    "WantedBy=default.target\n"
)
unit.chmod(0o600)
PY
    # Validate the generated unit BEFORE attempting to reload/start it.
    # This catches bad WorkingDirectory, ExecStart and unsupported directives.
    if command -v systemd-analyze >/dev/null 2>&1; then
      if ! systemd-analyze verify "$unit_dir/clipforge-config-agent.service"; then
        echo "Invalid systemd unit. Review the diagnostics above; no container was restarted." >&2
        exit 1
      fi
    fi
    if ! systemctl --user daemon-reload; then
      echo "Could not reload user systemd service definitions." >&2
      exit 1
    fi
    # A first-time install may not have a failed unit to reset.
    systemctl --user reset-failed clipforge-config-agent.service >/dev/null 2>&1 || true
    if ! systemctl --user enable --now clipforge-config-agent.service; then
      echo "User systemd service unavailable; start the agent manually in a separate shell:" >&2
      echo "  cd '$repo' && .venv/bin/python -m clipforge.config_agent --root '$repo'" >&2
      exit 1
    fi
    # systemd can report "active" before Python has bound its Unix socket.
    # Wait for the socket AND an authenticated read, never just the unit state.
    agent_ready=0
    for attempt in {1..40}; do
      if [[ -S private-auth/config-agent.sock ]] &&
         CLIPFORGE_CONFIG_AGENT_DIR="$repo/private-auth" .venv/bin/python -c 'from clipforge.config_agent import request_agent; response = request_agent("read"); assert response.get("ok")' >/dev/null 2>&1; then
        agent_ready=1
        break
      fi
      sleep 0.25
    done
    if [[ "$agent_ready" != 1 ]]; then
      echo "Host configuration agent did not become ready. No containers were restarted." >&2
      systemctl --user status clipforge-config-agent.service --no-pager --full >&2 || true
      exit 1
    fi
    CLIPFORGE_CONFIG_AGENT_DIR="$repo/private-auth" .venv/bin/python - <<'PY'
from clipforge.config_agent import request_agent
result = request_agent("read")
if not result.get("ok"):
    raise SystemExit("Agent could not read host configuration")
print("Host agent verified, configuration revision:", result["data"]["revision"])
PY
    echo "PREPARED — No Docker containers have been restarted."
    echo "Next step: bash scripts/phase1-activate.sh --deploy"
    ;;
  --deploy)
    check_queue  # Fail closed if API is unavailable or job status is unknown.
    [[ -S private-auth/config-agent.sock ]] || {
      echo "Host configuration agent not running. Run --prepare first." >&2; exit 1;
    }
    docker compose --profile ai config -q
    docker compose --profile ai up -d --build --no-deps clipforge
    echo "Waiting for ClipForge API readiness..."
    wait_api_ready
    printf '\nChecking appearance and Settings UI...\n'
    local_settings_html="$(curl -fsS --max-time 15 http://127.0.0.1:8765/settings)"
    [[ "$local_settings_html" == *'id="appearance-theme"'* ]] || {
      echo "Theme switcher not found" >&2; exit 1;
    }
    [[ "$local_settings_html" == *'id="admin-environment-panel"'* ]] || {
      echo "Environment settings UI not found" >&2; exit 1;
    }
    # HTML alone is insufficient: ensure the non-root web container can access
    # the host agent token/socket via the expected supplementary group.
    docker compose --profile ai exec -T clipforge python - <<'PY'
from clipforge.config_agent import request_agent
response = request_agent("read")
if not response.get("ok"):
    raise SystemExit("The web container cannot read the host configuration agent")
print("Container-to-host environment agent: connected")
PY
    echo "DEPLOYED — Themes, Settings and host agent connection verified."
    ;;
  --verify)
    wait_api_ready
    docker compose --profile ai ps
    if [[ -S private-auth/config-agent.sock ]]; then
      echo "Host config agent socket: present"
      docker compose --profile ai exec -T clipforge python - <<'PY'
from clipforge.config_agent import request_agent
response = request_agent("read")
if not response.get("ok"):
    raise SystemExit("Host config agent cannot be reached from ClipForge container")
print("Settings environment agent: connected")
PY
    else
      echo "Host config agent socket: missing (Settings editor unavailable)" >&2
      exit 1
    fi
    ;;
esac
