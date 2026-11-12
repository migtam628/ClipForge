"""Local, token-authenticated Unix-socket host configuration agent.

Run as the project owner on the HOST, never with root/Docker socket privileges.
The web container mounts only private-auth read-only and cannot edit host files
directly. Agent edits only <specified project root>/.env and only registry keys.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import socket
import socketserver
from pathlib import Path

from clipforge.environment import public_snapshot, restore_env_revision, update_env, validate_patch

MAX_REQUEST = 32768


def request_agent(operation: str, payload: dict | None = None) -> dict:
    """Called by the web container through the read-only private-auth mount."""
    root = Path(os.environ.get("CLIPFORGE_CONFIG_AGENT_DIR", "/run/clipforge-auth"))
    path = root / "config-agent.sock"
    keyfile = root / "config-agent.token"
    try:
        token = keyfile.read_text(encoding="ascii").strip()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(5)
            conn.connect(str(path))
            raw = json.dumps({"token": token, "operation": operation,
                              "payload": payload or {}}).encode("utf-8") + b"\n"
            if len(raw) > MAX_REQUEST:
                raise ValueError("Configuration update too large")
            conn.sendall(raw)
            data = b""
            while b"\n" not in data:
                part = conn.recv(8192)
                if not part or len(data) + len(part) > 100000:
                    raise ValueError("Unexpected configuration agent response")
                data += part
        reply = json.loads(data.split(b"\n", 1)[0])
        if not isinstance(reply, dict):
            raise ValueError("Unexpected configuration agent response")
        return reply
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        # Never include raw token, socket payload or credentials in browser errors.
        raise ConnectionError("Host configuration agent is unavailable") from exc


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            data = self.rfile.readline(MAX_REQUEST + 1)
            if not data.endswith(b"\n") or len(data) > MAX_REQUEST:
                return
            request = json.loads(data)
            if not isinstance(request, dict):
                return
            if not hmac.compare_digest(str(request.get("token", "")), self.server.token):
                self._send({"ok": False, "error": "Not authorized"})
                return
            action = request.get("operation")
            if action == "read":
                self._send({"ok": True, "data": public_snapshot(self.server.env_file)})
            elif action == "update":
                args = request.get("payload") or {}
                if not isinstance(args, dict):
                    raise ValueError("Invalid configuration request")
                changes = validate_patch(args.get("changes", {}))
                revision = update_env(self.server.env_file, changes, args.get("revision", ""))
                self._send({"ok": True, "data": {"revision": revision, "pending_apply": True,
                                                  "changed_keys": sorted(changes)}})
            else:
                self._send({"ok": False, "error": "Unknown operation"})
        except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            # Validation errors name only the field, never echo its value.
            self._send({"ok": False, "error": str(exc)})

    def _send(self, response: dict) -> None:
        self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, socket_path: Path, env_file: Path, token: str):
        self.env_file = env_file
        self.token = token
        super().__init__(str(socket_path), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="ClipForge host-only .env manager")
    parser.add_argument("--root", type=Path, default=Path.cwd(),
                        help="ClipForge host project directory")
    parser.add_argument("--setup", action="store_true",
                        help="Initialize token and permissions only")
    parser.add_argument("--restore-revision", default="",
                        help="Host-only restore of an encrypted .env revision; never via the web API")
    args = parser.parse_args()
    root = args.root.resolve()
    env_file = root / ".env"
    private = root / "private-auth"
    if not env_file.is_file() or env_file.is_symlink():
        parser.error("The real host project .env file must exist (no symlinks)")
    if args.restore_revision:
        revision = restore_env_revision(env_file, args.restore_revision)
        print("Restored host .env revision:", revision)
        print("Docker services are NOT restarted. Verify idle queue before applying.")
        return
    private.mkdir(mode=0o750, exist_ok=True)
    os.chmod(private, 0o750)
    keyfile = private / "config-agent.token"
    if not keyfile.exists():
        fd = os.open(keyfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(secrets.token_urlsafe(48) + "\n")
    if keyfile.is_symlink():
        parser.error("Refusing symlink token file")
    os.chmod(keyfile, 0o640)
    if args.setup:
        print("Agent configured. Ensure container UID 10001 has group access.")
        print("Set CLIPFORGE_CONFIG_GROUP_GID to your host primary group ID (id -g).")
        return
    token = keyfile.read_text(encoding="ascii").strip()
    socket_path = private / "config-agent.sock"
    if socket_path.is_symlink():
        parser.error("Refusing socket symlink")
    if socket_path.exists():
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(1)
                client.connect(str(socket_path))
                parser.error("Configuration agent already running")
        except (ConnectionError, OSError):
            socket_path.unlink()
    with Server(socket_path, env_file, token) as server:
        os.chmod(socket_path, 0o660)
        print("ClipForge configuration agent listening on private Unix socket.")
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            socket_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
