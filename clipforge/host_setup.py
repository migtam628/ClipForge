"""Host-only setup for ClipForge's private environment editor.

Run as the owner of the host project, not inside Docker. Passwords are entered
via a no-echo terminal prompt and never included in shell history, logs or Git.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import re
from pathlib import Path

from argon2 import PasswordHasher

from clipforge.environment import _archive_encrypted, _atomic_replace, _exclusive_lock


def install_admin_credentials(path: Path, password: str, group_id: int, *,
                              rotate: bool = False) -> str:
    """Replace a host .env admin hash atomically, backing up the previous revision."""
    if path.name != ".env" or path.is_symlink() or not path.is_file():
        raise ValueError("Host .env must be an existing regular file")
    if len(password) < 16 or len(password) > 1024:
        raise ValueError("Administrator password must be 16–1024 characters")
    if not 1 <= group_id <= 65535:
        raise ValueError("Host group ID is out of range")
    ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
    digest = ph.hash(password)
    # Argon2id PHC output uses only characters accepted by Docker Compose .env.
    if not re.fullmatch(r"\$argon2id\$[A-Za-z0-9$=+,./-]+", digest):
        raise ValueError("Unexpected password hash format")
    changes = {
        "CLIPFORGE_ADMIN_PASSWORD_HASH": f"'{digest}'",
        "CLIPFORGE_CONFIG_GROUP_GID": str(group_id),
    }
    with _exclusive_lock(path):
        original = path.read_bytes()
        if len(original) > 131072:
            raise ValueError("Environment file is unexpectedly large")
        existing_lines = original.decode("utf-8").splitlines(keepends=True)
        seen: set[str] = set()
        updated: list[str] = []
        for line in existing_lines:
            match = re.fullmatch(r"\s*([A-Z][A-Z0-9_]*)=(.*)", line.strip())
            if match and match.group(1) in changes:
                key = match.group(1)
                if key in seen:
                    raise ValueError("Duplicate sensitive configuration key")
                seen.add(key)
                if key == "CLIPFORGE_ADMIN_PASSWORD_HASH" and match.group(2) and not rotate:
                    # Never unexpectedly invalidate an existing admin login.
                    updated.append(line)
                    continue
                updated.append(f"{key}={changes[key]}\n")
            else:
                updated.append(line)
        for key, value in changes.items():
            if key not in seen:
                if updated and not updated[-1].endswith("\n"):
                    updated.append("\n")
                updated.append(f"{key}={value}\n")
        new_bytes = "".join(updated).encode("utf-8")
        if new_bytes != original:
            revision = hashlib.sha256(original).hexdigest()[:16]
            _archive_encrypted(path, original, revision)
            _atomic_replace(path, new_bytes)
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize ClipForge admin configuration")
    parser.add_argument("--root", type=Path, default=Path.cwd(),
                        help="Host directory containing the real .env")
    parser.add_argument("--rotate", action="store_true",
                        help="Explicitly replace an existing admin password hash")
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("Run as the regular ClipForge project owner, not root")
    root = args.root.resolve()
    path = root / ".env"
    if not path.is_file() or path.is_symlink():
        parser.error("Existing regular .env file is required")
    password = getpass.getpass("New administrator password (16+ characters): ")
    confirm = getpass.getpass("Confirm administrator password: ")
    if password != confirm:
        parser.error("Passwords do not match")
    gid = os.getgid()
    try:
        revision = install_admin_credentials(path, password, gid, rotate=args.rotate)
    except ValueError as exc:
        parser.error(str(exc))
    print("Administrator configuration prepared. Revision:", revision)
    print("Host group ID:", gid)
    print("Password/hash not displayed. Docker must be recreated when queue is idle.")


if __name__ == "__main__":
    main()
