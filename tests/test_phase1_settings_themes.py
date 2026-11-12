"""Phase 1 appearance, admin access, and host environment safety checks."""
import hashlib
import threading

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from clipforge import admin_auth
from clipforge import app as module
from clipforge.environment import (
    ENVIRONMENT_FIELDS,
    public_snapshot,
    read_env,
    update_env,
    validate_patch,
)


def test_three_themes_are_available_on_all_pages():
    client = TestClient(module.app)
    for path in ("/studio", "/library", "/publishing", "/activity",
                 "/analytics", "/settings"):
        html = client.get(path).text
        assert 'id="appearance-theme"' in html
        assert 'data-theme-select' in html
        assert '/assets/theme.js' in html
        assert '/assets/themes.css' in html
    html = client.get("/settings").text
    for name in ("precision", "editorial", "control"):
        assert f'data-theme-choice="{name}"' in html
    script = client.get("/assets/theme.js").text
    assert "localStorage.setItem(KEY, theme)" in script
    assert "document.documentElement" in script
    css = client.get("/assets/themes.css").text
    assert 'data-theme="editorial"' in css
    assert 'data-theme="control"' in css
    assert "prefers-reduced-motion" in css


def test_registry_covers_existing_and_new_deployment_fields():
    assert len(ENVIRONMENT_FIELDS) >= 25
    for key in (
        "CLIPFORGE_OLLAMA_URL",
        "CLIPFORGE_ELEVENLABS_API_KEY",
        "CLIPFORGE_YOUTUBE_REFRESH_TOKEN",
        "TS_AUTHKEY",
        "CLIPFORGE_ADMIN_PASSWORD_HASH",
        "CLIPFORGE_CONFIG_GROUP_GID",
    ):
        assert key in ENVIRONMENT_FIELDS
    with pytest.raises(ValueError):
        validate_patch({"CLIPFORGE_ADMIN_PASSWORD_HASH": "attacker"})
    with pytest.raises(ValueError):
        validate_patch({"UNRECOGNIZED_VARIABLE": "unsafe"})
    with pytest.raises(ValueError):
        validate_patch({"CLIPFORGE_ELEVENLABS_API_KEY": "line\nbreak"})
    with pytest.raises(ValueError):
        validate_patch({"CLIPFORGE_DATA_DIR": "/tmp/evil"})
    with pytest.raises(ValueError):
        validate_patch({"CLIPFORGE_YOUTUBE_COOKIES_FILE": "/etc/passwd"})
    assert validate_patch({"CLIPFORGE_FFMPEG_THREADS": "3"}) == {
        "CLIPFORGE_FFMPEG_THREADS": "3"}


def test_host_env_update_is_atomic_revision_checked_and_masked(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# protect comments\nCLIPFORGE_AI_MODEL=old-model\n"
                    "CLIPFORGE_ELEVENLABS_API_KEY=very-sensitive\n")
    revision = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    new_revision = update_env(path, {
        "CLIPFORGE_AI_MODEL": "new-model",
        "CLIPFORGE_ELEVENLABS_API_KEY": "updated-private-token",
        "CLIPFORGE_WHISPER_MODEL": "small",
    }, revision)
    assert new_revision != revision
    values = read_env(path)
    assert values["CLIPFORGE_AI_MODEL"] == "new-model"
    assert values["CLIPFORGE_WHISPER_MODEL"] == "small"
    assert values["CLIPFORGE_ELEVENLABS_API_KEY"] == "updated-private-token"
    assert "# protect comments\n" in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    encrypted_backup = tmp_path / '.clipforge-env-archive' / (revision + '.enc')
    assert encrypted_backup.is_file()
    assert b'very-sensitive' not in encrypted_backup.read_bytes()
    assert (encrypted_backup.stat().st_mode & 0o777) == 0o600
    assert (tmp_path / '.clipforge-env-archive.key').is_file()
    snapshot = public_snapshot(path)
    assert snapshot["revision"] == new_revision
    assert "updated-private-token" not in str(snapshot)
    secret_entry = next(x for x in snapshot["entries"]
                        if x["key"] == "CLIPFORGE_ELEVENLABS_API_KEY")
    assert secret_entry["configured"] and secret_entry["value"] == ""
    with pytest.raises(ValueError, match="Environment changed"):
        update_env(path, {"CLIPFORGE_AI_MODEL": "bad"}, revision)
    assert path.read_text().count("CLIPFORGE_AI_MODEL=") == 1


def test_admin_routes_fail_closed_before_password_setup(monkeypatch):
    monkeypatch.delenv("CLIPFORGE_ADMIN_PASSWORD_HASH", raising=False)
    client = TestClient(module.app, base_url="https://testserver")
    session = client.get("/api/admin/session").json()
    assert session == {"configured": False, "authenticated": False}
    assert client.get("/api/admin/environment").status_code == 503
    assert client.post("/api/admin/login", json={"password": "anything"}).status_code == 503


def test_authentication_csrf_and_no_direct_browser_secrets(monkeypatch):
    admin_auth.ATTEMPTS.clear()
    admin_auth.SESSIONS.clear()
    monkeypatch.setenv("CLIPFORGE_ADMIN_PASSWORD_HASH", PasswordHasher().hash(
        "a-long-private-test-password"))
    calls = []

    def fake_agent(operation, payload=None):
        calls.append((operation, payload))
        if operation == "read":
            return {"ok": True, "data": {
                "revision": "aaaaaaaaaaaaaaaa",
                "entries": [{
                    "key": "CLIPFORGE_ELEVENLABS_API_KEY", "secret": True,
                    "type": "secret", "editable": True, "section": "Narration",
                    "description": "ElevenLabs API key", "configured": True,
                    "value": "",
                }],
            }}
        return {"ok": True, "data": {
            "revision": "bbbbbbbbbbbbbbbb", "pending_apply": True,
            "changed_keys": ["CLIPFORGE_ELEVENLABS_API_KEY"],
        }}

    monkeypatch.setattr(module, "request_agent", fake_agent)
    client = TestClient(module.app, base_url="https://testserver")
    assert client.get("/api/admin/environment").status_code == 401
    login = client.post("/api/admin/login", json={
        "password": "a-long-private-test-password"})
    assert login.status_code == 200
    assert "httponly" in login.headers["set-cookie"].lower()
    assert "secure" in login.headers["set-cookie"].lower()
    csrf = login.json()["csrf"]
    assert client.get("/api/admin/session").json()["authenticated"]
    view = client.get("/api/admin/environment")
    assert view.status_code == 200
    assert view.json()["entries"][0]["value"] == ""
    payload = {"revision": "aaaaaaaaaaaaaaaa",
               "changes": {"CLIPFORGE_ELEVENLABS_API_KEY": "new-secret"}}
    assert client.patch("/api/admin/environment", json=payload).status_code == 403
    response = client.patch("/api/admin/environment", json=payload,
                            headers={"x-clipforge-csrf": csrf})
    assert response.status_code == 200
    assert "new-secret" not in response.text
    assert calls[-1][0] == "update"
    assert client.post("/api/admin/logout", headers={
        "x-clipforge-csrf": csrf}).status_code == 200
    assert client.get("/api/admin/environment").status_code == 401


def test_unix_agent_writes_real_host_env_and_masks_secrets(tmp_path, monkeypatch):
    from clipforge.config_agent import Server, request_agent

    private = tmp_path / "private-auth"
    private.mkdir()
    (private / "config-agent.token").write_text("test-agent-token\n")
    path = tmp_path / ".env"
    path.write_text("CLIPFORGE_AI_MODEL=old-model\n"
                    "CLIPFORGE_ELEVENLABS_API_KEY=secret-old\n")
    socket_path = private / "config-agent.sock"
    monkeypatch.setenv("CLIPFORGE_CONFIG_AGENT_DIR", str(private))
    server = Server(socket_path, path, "test-agent-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        snapshot = request_agent("read")
        assert snapshot["ok"]
        assert "secret-old" not in str(snapshot)
        revision = snapshot["data"]["revision"]
        updated = request_agent("update", {
            "revision": revision,
            "changes": {"CLIPFORGE_ELEVENLABS_API_KEY": "secret-new",
                        "CLIPFORGE_AI_MODEL": "new-model"},
        })
        assert updated["ok"]
        assert updated["data"]["pending_apply"]
        assert "secret-new" not in str(updated)
        assert read_env(path)["CLIPFORGE_ELEVENLABS_API_KEY"] == "secret-new"
        assert read_env(path)["CLIPFORGE_AI_MODEL"] == "new-model"
        assert "secret-new" not in str(request_agent("read"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
