"""Host activation safety: no silent jobs disruption, no printed admin credentials."""
from __future__ import annotations

import hashlib
import subprocess
import sys
import shutil
from pathlib import Path

import pytest
from argon2 import PasswordHasher

from clipforge.environment import read_env, restore_env_revision
from clipforge.host_setup import install_admin_credentials

ROOT = Path(__file__).resolve().parents[1]


def test_activation_shell_is_parseable_and_checks_queue():
    script = ROOT / "scripts" / "phase1-activate.sh"
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    text = script.read_text()
    assert "check_queue" in text
    assert "docker compose --profile ai up -d --build --no-deps clipforge" in text
    assert text.index("check_queue") < text.index(
        "docker compose --profile ai up -d --build --no-deps clipforge")
    assert "git pull --ff-only" in text
    assert "docker compose down -v" not in text
    assert "docker compose down" not in text


def test_host_setup_creates_secure_hash_in_env_without_plaintext(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# existing configuration\nCLIPFORGE_AI_MODEL=llama3.2:3b\n")
    old_revision = hashlib.sha256(env.read_bytes()).hexdigest()[:16]
    password = "example-long-admin-password-not-to-share"
    revision = install_admin_credentials(env, password, 1234)
    assert revision != old_revision
    assert "example-long-admin-password" not in env.read_text()
    values = read_env(env)
    assert values["CLIPFORGE_CONFIG_GROUP_GID"] == "1234"
    PasswordHasher().verify(values["CLIPFORGE_ADMIN_PASSWORD_HASH"], password)
    assert env.stat().st_mode & 0o777 == 0o600
    backup = tmp_path / ".clipforge-env-archive" / (old_revision + ".enc")
    assert backup.exists()
    assert b"CLIPFORGE_AI_MODEL" not in backup.read_bytes()


def test_host_setup_requires_explicit_rotation(tmp_path):
    env = tmp_path / ".env"
    env.write_text("CLIPFORGE_ADMIN_PASSWORD_HASH='existing-hash'\n"
                   "CLIPFORGE_CONFIG_GROUP_GID=1000\n")
    install_admin_credentials(env, "a-very-long-new-admin-passphrase", 2000)
    assert read_env(env)["CLIPFORGE_ADMIN_PASSWORD_HASH"] == "existing-hash"
    assert read_env(env)["CLIPFORGE_CONFIG_GROUP_GID"] == "2000"
    install_admin_credentials(env, "another-very-long-admin-passphrase", 2000,
                              rotate=True)
    PasswordHasher().verify(read_env(env)["CLIPFORGE_ADMIN_PASSWORD_HASH"],
                            "another-very-long-admin-passphrase")


def test_host_setup_rejects_invalid_requests(tmp_path):
    env = tmp_path / ".env"
    env.write_text("CLIPFORGE_AI_MODEL=base\n")
    for password, gid in (("short", 1000), ("long-enough-password", -1)):
        with pytest.raises(ValueError):
            install_admin_credentials(env, password, gid)
    link = tmp_path / "linked.env"
    link.symlink_to(env)
    with pytest.raises(ValueError):
        install_admin_credentials(link, "a-very-long-new-password", 1000)
    assert "CLIPFORGE_ADMIN_PASSWORD_HASH" not in env.read_text()


def test_previous_environment_can_be_restored_after_admin_setup(tmp_path):
    env = tmp_path / ".env"
    env.write_text("CLIPFORGE_AI_MODEL=llama3.2:3b\n")
    prior = hashlib.sha256(env.read_bytes()).hexdigest()[:16]
    install_admin_credentials(env, "another-long-admin-password", 1000)
    assert restore_env_revision(env, prior) == prior
    assert read_env(env)["CLIPFORGE_AI_MODEL"] == "llama3.2:3b"
    assert "CLIPFORGE_ADMIN_PASSWORD_HASH" not in env.read_text()


def test_generated_systemd_unit_has_valid_absolute_paths(tmp_path):
    """Regression: WorkingDirectory with literal quotes causes 'bad-setting'."""
    script = (ROOT / "scripts" / "phase1-activate.sh").read_text()
    opening = 'python3 - "$repo" "$unit_dir/clipforge-config-agent.service" <<\'PY\'\n'
    assert script.count(opening) == 1
    generator = script.split(opening, 1)[1].split("\nPY\n", 1)[0]
    root = tmp_path / "ClipForge"
    unit = tmp_path / "clipforge-config-agent.service"
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    result = subprocess.run(
        [sys.executable, "-", str(root), str(unit)],
        input=generator, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    contents = unit.read_text()
    assert f"WorkingDirectory={root}" in contents
    assert f'WorkingDirectory="{root}"' not in contents
    assert f"ExecStart={python} -m clipforge.config_agent --root {root}" in contents
    if shutil.which("systemd-analyze"):
        verified = subprocess.run(
            ["systemd-analyze", "verify", str(unit)],
            capture_output=True, text=True, check=False,
        )
        assert verified.returncode == 0, verified.stderr
