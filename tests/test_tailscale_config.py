"""Static safeguards for private Tailscale Docker deployment."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_tailnet_serve_restricts_access_and_proxies_app_port():
    config = json.loads((ROOT / "tailscale/serve.json").read_text())
    host = "${TS_CERT_DOMAIN}:443"
    assert config["TCP"]["443"]["HTTPS"] is True
    assert config["Web"][host]["Handlers"]["/"]["Proxy"] == "http://127.0.0.1:8765"
    assert config["AllowFunnel"][host] is False


def test_tailnet_compose_has_no_published_port_and_uses_persistent_identity():
    config = (ROOT / "compose.tailscale.yaml").read_text()
    assert "network_mode: service:tailscale" in config
    assert "TS_AUTH_ONCE:" in config
    assert "TS_SERVE_CONFIG: /config/serve.json" in config
    assert "TS_STATE_DIR: /var/lib/tailscale" in config
    assert "tailscale_state:/var/lib/tailscale" in config
    assert "clipforge_data:/data" in config
    assert "    ports:" not in config
    assert "    network_mode: host" not in config


def test_auth_key_not_committed_and_local_docker_remains_private():
    assert ".env\n" in (ROOT / ".gitignore").read_text()
    local = (ROOT / "compose.yaml").read_text()
    assert '"127.0.0.1:8765:8765"' in local
