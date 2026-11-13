"""Strict, inspectable environment registry for ClipForge's host-side manager.

Never execute .env content as shell. Secrets are never returned over the API.
Host writes are performed exclusively by the separate Unix-socket config agent.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

# key: (section, type, secret, description)
ENVIRONMENT_FIELDS = {
    "CLIPFORGE_OLLAMA_URL": ("AI models", "url", False, "Local Ollama API URL"),
    "CLIPFORGE_AI_MODEL": ("AI models", "text", False, "Local text model"),
    "CLIPFORGE_VISION_MODEL": ("AI models", "text", False, "Local visual model"),
    "CLIPFORGE_WHISPER_MODEL": ("AI models", "whisper", False, "Transcription model"),
    "CLIPFORGE_AI_BATCH_SIZE": ("AI models", "int:1:32", False, "Windows scored per AI batch"),
    "CLIPFORGE_AI_TIMEOUT": ("AI models", "int:30:3600", False, "Model request timeout in seconds"),
    "CLIPFORGE_AI_RETRIES": ("AI models", "int:0:10", False, "Model retries"),
    "CLIPFORGE_FFMPEG_THREADS": ("Video processing", "int:1:32", False, "Video rendering threads"),
    "CLIPFORGE_ELEVENLABS_API_KEY": ("Narration", "secret", True, "ElevenLabs API key"),
    "CLIPFORGE_ELEVENLABS_VOICE_ID": ("Narration", "text", False, "ElevenLabs voice ID"),
    "CLIPFORGE_ELEVENLABS_MODEL": ("Narration", "text", False, "ElevenLabs model"),
    "CLIPFORGE_YOUTUBE_CLIENT_ID": ("Publishing", "text", False, "Google OAuth client ID"),
    "CLIPFORGE_YOUTUBE_CLIENT_SECRET": ("Publishing", "secret", True, "Google OAuth client secret"),
    "CLIPFORGE_YOUTUBE_REFRESH_TOKEN": ("Publishing", "secret", True, "Google refresh token"),
    "CLIPFORGE_INSTAGRAM_TOKEN": ("Publishing", "secret", True, "Instagram publishing token"),
    "CLIPFORGE_INSTAGRAM_USER_ID": ("Publishing", "text", False, "Instagram business ID"),
    "CLIPFORGE_FACEBOOK_PAGE_ACCESS_TOKEN": ("Publishing", "secret", True, "Facebook Page token"),
    "CLIPFORGE_TIKTOK_ACCESS_TOKEN": ("Publishing", "secret", True, "TikTok access token"),
    "CLIPFORGE_META_API_VERSION": ("Publishing", "meta-version", False, "Meta Graph API version"),
    "CLIPFORGE_DOWNLOAD_IP_FAMILY": ("Import", "ip-family", False, "Import IP preference"),
    "CLIPFORGE_YOUTUBE_COOKIES_FILE": ("Import", "container-path", False, "Read-only container cookie path"),
    "CLIPFORGE_DATA_DIR": ("Infrastructure", "readonly", False, "Container data mount: migration required"),
    "TS_AUTHKEY": ("Infrastructure", "secret", True, "Tailscale sidecar bootstrap credential"),
    "CLIPFORGE_ADMIN_PASSWORD_HASH": ("Security", "readonly", True,
                                      "Admin Argon2id hash: change via password rotation"),
    "CLIPFORGE_CONFIG_GROUP_GID": ("Infrastructure", "int:1:65535", False,
                                   "Host group access to configuration Unix socket"),
}

KEY_PATTERN = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
SAFE_VALUE_PATTERN = re.compile(r"^[a-zA-Z0-9_./:@+,-]*$")


def field_schema() -> list[dict]:
    return [
        {"key": key, "section": section, "type": kind, "secret": secret,
         "description": description, "editable": kind != "readonly"}
        for key, (section, kind, secret, description) in ENVIRONMENT_FIELDS.items()
    ]


def validate_patch(changes: dict[str, str]) -> dict[str, str]:
    if not isinstance(changes, dict) or len(changes) > len(ENVIRONMENT_FIELDS):
        raise ValueError("Expected a small object of environment values")
    valid: dict[str, str] = {}
    for key, value in changes.items():
        if key not in ENVIRONMENT_FIELDS:
            raise ValueError("Unknown configuration key")
        if not isinstance(value, str) or len(value) > 4096 or any(
            ord(ch) < 32 or ord(ch) == 127 for ch in value
        ):
            raise ValueError(f"Invalid characters in {key}")
        kind = ENVIRONMENT_FIELDS[key][1]
        if kind == "readonly":
            raise ValueError(f"{key} requires a dedicated migration")
        if kind.startswith("int:"):
            _, low, high = kind.split(":")
            if not value.isascii() or not value.isdigit() or not (
                int(low) <= int(value) <= int(high)
            ):
                raise ValueError(f"{key} must be between {low} and {high}")
        if kind == "whisper" and value not in {"tiny", "base", "small", "medium"}:
            raise ValueError("Invalid Whisper model")
        if kind == "ip-family" and value not in {"auto", "ipv4", "ipv6"}:
            raise ValueError("Invalid IP family")
        if kind == "meta-version" and not re.fullmatch(r"v\d+\.\d+", value):
            raise ValueError("Invalid Meta API version")
        if kind == "url" and not re.fullmatch(
            r"https?://(?:ollama|localhost|127\.0\.0\.1|host\.docker\.internal)(?::[0-9]{1,5})?/?",
            value,
        ):
            raise ValueError("Ollama URL must use a trusted local host")
        if kind == "container-path" and (
            not value.startswith("/run/clipforge-auth/") or ".." in value
        ):
            raise ValueError("Cookie path must stay in read-only private-auth mount")
        valid[key] = value
    return valid


def _decode(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("\\'", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value.split(" #", 1)[0].strip()


def read_env(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError("Host .env file must exist and cannot be a symlink")
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = KEY_PATTERN.fullmatch(line.strip())
        if match:
            key, raw = match.groups()
            if key in ENVIRONMENT_FIELDS:
                result[key] = _decode(raw)
    return result


def public_snapshot(path: Path) -> dict:
    values = read_env(path)
    return {
        "revision": hashlib.sha256(path.read_bytes()).hexdigest()[:16],
        "entries": [
            {**entry, "configured": bool(values.get(entry["key"])),
             "value": "" if entry["secret"] else values.get(entry["key"], "")}
            for entry in field_schema()
        ],
        "apply_required": True,
    }


def _encode(value: str) -> str:
    if SAFE_VALUE_PATTERN.fullmatch(value):
        return value
    return "'" + value.replace("'", "\\'") + "'"


@contextmanager
def _exclusive_lock(path: Path):
    with open(path.parent / ".env.lock", "a+", encoding="utf-8") as lock:
        os.chmod(lock.fileno(), 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _archive_encrypted(path: Path, original: bytes, revision: str) -> None:
    """Store encrypted pre-update snapshot; never leave plaintext copies."""
    from cryptography.fernet import Fernet

    keyfile = path.parent / ".clipforge-env-archive.key"
    archive = path.parent / ".clipforge-env-archive"
    if keyfile.is_symlink() or archive.is_symlink():
        raise ValueError("Refusing symlink in environment backup storage")
    if not keyfile.exists():
        try:
            fd = os.open(keyfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(Fernet.generate_key())
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            pass
    if keyfile.stat().st_mode & 0o077:
        raise ValueError("Environment archive key permissions must be 0600")
    key = keyfile.read_bytes().strip()
    archive.mkdir(mode=0o700, exist_ok=True)
    os.chmod(archive, 0o700)
    backup = archive / (revision + ".enc")
    if backup.exists():
        return
    encrypted = Fernet(key).encrypt(original)
    fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encrypted)
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_replace(path: Path, data: bytes) -> None:
    fd, temp = tempfile.mkstemp(prefix=".clipforge-env-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
        dirfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def restore_env_revision(path: Path, revision: str) -> str:
    """Host CLI only: restore authenticated encrypted revision atomically."""
    from cryptography.fernet import Fernet, InvalidToken

    if not re.fullmatch(r"[0-9a-f]{16}", revision or ""):
        raise ValueError("Invalid revision")
    if path.name != ".env" or path.is_symlink() or not path.is_file():
        raise ValueError("Only existing regular host .env can be restored")
    with _exclusive_lock(path):
        keyfile = path.parent / ".clipforge-env-archive.key"
        archive = path.parent / ".clipforge-env-archive" / (revision + ".enc")
        if keyfile.is_symlink() or archive.is_symlink() or not archive.is_file():
            raise ValueError("Archived environment revision not found")
        try:
            recovered = Fernet(keyfile.read_bytes().strip()).decrypt(archive.read_bytes())
        except (ValueError, InvalidToken) as exc:
            raise ValueError("Environment backup cannot be decrypted") from exc
        if hashlib.sha256(recovered).hexdigest()[:16] != revision:
            raise ValueError("Encrypted environment backup does not match revision")
        current = path.read_bytes()
        current_revision = hashlib.sha256(current).hexdigest()[:16]
        if current_revision == revision:
            return revision
        _archive_encrypted(path, current, current_revision)
        _atomic_replace(path, recovered)
        return revision


def update_env(path: Path, changes: dict[str, str], expected_revision: str) -> str:
    """Atomic, symlink-safe .env patch. Caller must authenticate and authorize."""
    changes = validate_patch(changes)
    if not changes:
        raise ValueError("No changes specified")
    if not re.fullmatch(r"[0-9a-f]{16}", expected_revision or ""):
        raise ValueError("Expected revision is required")
    if path.name != ".env" or path.is_symlink() or not path.is_file():
        raise ValueError("Only an existing regular .env file can be edited")
    with _exclusive_lock(path):
        original = path.read_bytes()
        revision = hashlib.sha256(original).hexdigest()[:16]
        if revision != expected_revision:
            raise ValueError("Environment changed; refresh before saving")
        lines = original.decode("utf-8").splitlines(keepends=True)
        found: set[str] = set()
        updated: list[str] = []
        for line in lines:
            match = KEY_PATTERN.fullmatch(line.strip())
            if match and match.group(1) in changes:
                key = match.group(1)
                if key in found:
                    raise ValueError("Duplicate environment key requires manual repair")
                found.add(key)
                updated.append(f"{key}={_encode(changes[key])}\n")
            else:
                updated.append(line)
        for key, value in changes.items():
            if key not in found:
                if updated and not updated[-1].endswith("\n"):
                    updated.append("\n")
                updated.append(f"{key}={_encode(value)}\n")
        # Protect the original with an encrypted host-only archive before replacement.
        data = "".join(updated).encode("utf-8")
        _archive_encrypted(path, original, revision)
        _atomic_replace(path, data)
        return hashlib.sha256(data).hexdigest()[:16]
