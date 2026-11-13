# ClipForge 2.0 — Phase 1 admin and host configuration setup

This release introduces three persistent UI themes and an administrator-only environment editor.
**The host agent is opt-in. It has no Docker socket, no shell-command endpoint,
and can change only typed keys in the existing host `.env`.**
Do not deploy until current video jobs finish.

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
