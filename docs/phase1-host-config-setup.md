# ClipForge 2.0 — Phase 1 admin and host configuration setup

This release introduces three persistent UI themes and an administrator-only environment editor.
**The host agent is opt-in. It has no Docker socket, no shell-command endpoint,
and can change only typed keys in the existing host `.env`.**
Do not deploy until current video jobs finish.

## Recommended two-command activation

This is the preferred one-time path on `mt-tech-server`. It provides stricter
safety checks than manually copying a credential hash and building a user service.
It does not delete volumes or change the existing video-processing engine.

**First, install the already-merged Phase 1 source without restarting Docker:**

```bash
cd ~/ClipForge
git status --short     # inspect any tracked local changes
git pull --ff-only
bash scripts/phase1-activate.sh --prepare
```

`--prepare` checks the main branch, creates/uses `.venv`, prompts without echo
for a **16+ character admin password only if no active Argon2id hash exists**,
sets the correct host group ID, prepares the restricted agent token and
systemd user service, and tests the agent's connection to the real `.env`.
A fresh `.env` revision is encrypted before any credential initialization.

**Only after the running job queue is idle:**

```bash
bash scripts/phase1-activate.sh --deploy
bash scripts/phase1-activate.sh --verify
```

`--deploy` refuses to restart if the job API cannot be read, has an unknown
status, or reports any nonterminal processing state. It rebuilds only ClipForge,
leaving existing Ollama and persistent Docker volumes untouched.

If `systemctl --user` is not available, the preparation script exits with a
manual host-agent start command instead of pretending setup succeeded.
A systemd user service may need login-linger enabled by the system administrator
to survive logout/reboot; do not silently change system login policy.

Open the HTTPS Tailnet address in Chrome, go to Settings → Appearance &
Layout, change all three themes, and use System & Environment to check the
administrator login and protected settings editor.

### Fixing `bad unit file setting` on existing installations

The earlier `--prepare` script generated a quoted `WorkingDirectory` value,
which systemd rejects as a non-absolute path. The corrected script writes an
unquoted absolute path and validates the service unit with
`systemd-analyze verify` before attempting to activate it.

If `clipforge-config-agent.service` is failing with `bad-setting`, update the
repository, then re-run preparation (this preserves an existing administrator
hash, clips, `.env` values, and Docker volumes):

```bash
cd ~/ClipForge
git pull --ff-only
bash scripts/phase1-activate.sh --prepare
systemctl --user status clipforge-config-agent.service --no-pager --full
```

The service can be debugged without printing secrets:

```bash
systemd-analyze verify "$HOME/.config/systemd/user/clipforge-config-agent.service"
systemctl --user cat clipforge-config-agent.service
journalctl --user -u clipforge-config-agent.service -n 35 --no-pager
```

Once `private-auth/config-agent.sock` exists and the host-agent read test
succeeds, deploy the app **only after the queue is idle**:

```bash
bash scripts/phase1-activate.sh --deploy
bash scripts/phase1-activate.sh --verify
```

A broken host agent affects the privileged Settings editor; it does not mean
the existing clip-processing Docker containers or Ollama are down.

### Agent active but socket initially missing

The systemd user service may enter `active (running)` before the Python
agent creates `private-auth/config-agent.sock`. The corrected
`--prepare` operation waits for an authenticated Unix-socket read rather
than checking socket existence immediately. A global `systemd-analyze verify`
may also print unrelated mount-unit diagnostics; confirm the ClipForge agent
status independently.

If the service is already active and no videos are processing, you can bypass
unnecessary password and virtualenv setup:

```bash
cd ~/ClipForge
git pull --ff-only
systemctl --user is-active clipforge-config-agent.service
test -S private-auth/config-agent.sock
CLIPFORGE_CONFIG_AGENT_DIR="$PWD/private-auth" .venv/bin/python - <<'PY'
from clipforge.config_agent import request_agent
r = request_agent("read")
assert r.get("ok")
print("Host configuration agent connected")
PY
bash scripts/phase1-activate.sh --deploy
bash scripts/phase1-activate.sh --verify
```

The deployment script refuses to restart if jobs are nonterminal, and now
verifies the container's authenticated connection to the host agent after
deploying. It does not return private `.env` values to the terminal.

### Credential rotation

If admin authentication is already configured, `--prepare` preserves the
existing password hash. To rotate it deliberately, use:

```bash
cd ~/ClipForge
.venv/bin/python -m clipforge.host_setup --root "$PWD" --rotate
```

It prompts for the new password, never echoes it or prints its hash, archives
the previous `.env` encrypted, and requires an idle-queue container recreate
before the new credential becomes active. Existing sessions are invalidated
by restart. Never put passwords in shell arguments or screenshots.

---

## 1. Select the right host project

Run from the folder hosting the existing Docker Compose deployment, for example:

```bash
cd ~/ClipForge
git pull --ff-only
id -g
```

Keep existing untracked files and media intact. Do not delete volumes or `private-auth`.

## 2. Prepare a host Python environment

The agent runs as the normal Linux user who owns `~/ClipForge/.env`,
not as root or a container:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Generate an administrator password hash without putting the password in shell history:

```bash
.venv/bin/python -m clipforge.admin_auth
```

Paste the generated **single-quoted Argon2id hash** as
`CLIPFORGE_ADMIN_PASSWORD_HASH='...'` in `~/ClipForge/.env`.
Avoid disclosing the hash in screenshots, terminal output sent to others, or Git.
This variable is deliberately not editable through the generic environment manager.

## 3. Set up the restricted host configuration agent

```bash
cd ~/ClipForge
chmod 600 .env
.venv/bin/python -m clipforge.config_agent --root "$PWD" --setup
```

Find your primary Linux group ID with `id -g`; set it as
`CLIPFORGE_CONFIG_GROUP_GID` in `.env`. If this is not 1000,
**change the default value**. The Compose service adds this supplemental group to
the non-root ClipForge worker (UID 10001). The socket and token live in
`./private-auth/`, which is mounted **read-only** into the application.

Do not expose `private-auth` over a web server, share, backup service without
encryption, or a public repository. The agent token must stay protected.

Start the agent in a host terminal (for a permanent deployment, make a restricted
systemd *user* unit that runs this exact command as the existing Linux user):

```bash
.venv/bin/python -m clipforge.config_agent --root "$PWD"
```

The socket is `./private-auth/config-agent.sock`; the token is
`./private-auth/config-agent.token`. They use group-restricted permissions.
The agent does not restart Docker or apply configuration automatically.

## 4. Start the updated web app

With the current processing queue idle:

```bash
docker compose --profile ai config -q
docker compose --profile ai up -d --build --no-deps clipforge
docker compose --profile ai ps
docker compose --profile ai exec -T clipforge id
curl -fsS http://127.0.0.1:8765/api/health
```

Use your existing HTTPS Tailscale address for administrator login.
The secure administrator cookie does not work over plain HTTP.

Open `Settings → System & Environment (.env)`. Sign in as administrator.
When the host agent is reachable, the editor loads masked secret metadata
and editable configuration values. Saving persists edits **atomically** to the
actual host `.env`, preserving comments and unrelated keys. A revision check
prevents overwriting someone else's changes. The API does not return secret values.

## 5. Apply configuration safely

`Saved to .env` does not mean `Applied to running services`.
Configuration must be activated after jobs are idle.
The current release intentionally does NOT have an automatic restart button.

Check the queue before a service restart:

```bash
python3 - <<'PY'
import json, urllib.request
jobs = json.load(urllib.request.urlopen("http://127.0.0.1:8765/api/jobs",timeout=10))
states = {"queued","downloading","analyzing","transcribing","selecting","rendering"}
active = [(j.get("id"),j.get("status")) for j in jobs if j.get("status") in states]
print("Active:",active)
if active: raise SystemExit("Stop: processing jobs are still active")
PY
```

Only after the check passes:

```bash
docker compose --profile ai up -d --no-deps --force-recreate clipforge
```

Then check health, AI model availability, voice configuration, publishing account
status and the updated Settings fields.

## Recovery from an environment mistake

Every successful .env update first stores an encrypted copy of the previous
version in `.clipforge-env-archive/<revision>.enc`; the key stays in
`.clipforge-env-archive.key` (mode 0600). Both are ignored by Git.
**Back up the encryption key separately using a secure backup process** or
the encrypted snapshots cannot be recovered after disk loss.

If a changed setting breaks the application, restore the previous revision on
the HOST (not through the browser):

```bash
cd ~/ClipForge
.venv/bin/python -m clipforge.config_agent --root "$PWD" \
  --restore-revision PREVIOUS_REVISION_FROM_SETTINGS
```

Restore changes only the host .env. Re-run the idle-job safety check and recreate
the application container separately to apply it.

## Security limitations and follow-up

- No unrestricted host filesystem, Docker daemon, shell commands, or public configuration API.
- Only recognized keys in `clipforge/environment.py` may be edited by the host agent.
- Administrator sign-in is additional protection for config endpoints. The rest
  of ClipForge's existing single-user routes still rely on private Tailscale
  access. Do not expose this deployment to the public Internet.
- The host agent can write `.env` and creates encrypted revision snapshots, but **automatic Apply,
  in-browser rollback, credential rotation, per-account OAuth token encryption, and a complete
  application-wide login wall are not included yet**. They are required for the
  full Phase 1 completion gate.
- The environment UI never displays existing secret values.
- Application theme choice is a browser preference and is not an environment
  variable. It does not trigger a server restart.
- `CLIPFORGE_DATA_DIR` is guarded because it is a mounted Docker storage path
  requiring a separate migration.
- If the host agent is offline or admin auth is not configured, the browser
  editor fails closed instead of claiming settings were saved.
