import json
import plistlib
import threading
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from filelock import FileLock

from aimemory.desktop import DesktopState, Handler, codex_mcp_configured, mcp_configured
from aimemory.health import watcher_health
from aimemory.installer import WatcherServiceStatus
from aimemory.menubar import ensure_login, login_path, set_login_enabled


@pytest.mark.parametrize("state,expected", [
    (None, "stopped"),
    ({"running": False, "last_error": "old"}, "stopped"),
    ({"running": True, "last_error": "failure"}, "error"),
    ({"running": True}, "starting"),
    ({"running": True, "last_success_at": "invalid"}, "starting"),
])
def test_health_handles_inactive_and_invalid_status(state, expected):
    assert watcher_health(state)["state"] == expected


def test_health_requires_a_recent_success_not_just_a_live_process():
    now = datetime.now(UTC)
    state = {"running": True, "last_success_at": (now - timedelta(seconds=20)).isoformat()}
    assert watcher_health(state, now)["state"] == "running"
    state["last_success_at"] = (now - timedelta(seconds=121)).isoformat()
    state["last_scan_at"] = now.isoformat()
    assert watcher_health(state, now)["state"] == "stale"
    assert watcher_health({"running": True, "started_at": now.isoformat()}, now)["state"] == "starting"


def test_login_registration_and_disable(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", "/Applications/AI Memory.app/Contents/MacOS/AI Memory")
    state = tmp_path / "memory" / "state"
    set_login_enabled(True, state)
    plist = plistlib.loads(login_path().read_bytes())
    assert plist["ProgramArguments"] == ["/Applications/AI Memory.app/Contents/MacOS/AI Memory", "--background"]
    assert plist["RunAtLoad"] is True
    assert "KeepAlive" not in plist  # Quitting the interface must be respected.
    assert plist["EnvironmentVariables"]["AI_MEMORY_HOME"] == str(state.parent)
    set_login_enabled(False, state)
    ensure_login(state)
    assert not login_path().exists()


def test_development_does_not_register_login(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delattr("sys.frozen", raising=False)
    ensure_login(tmp_path / "state")
    assert not login_path().exists()


def test_codex_mcp_status_does_not_claim_a_disabled_server_is_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("PATH", "")
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text('[mcp_servers.ai-memory]\ncommand="test"\nenabled=false\n')
    assert not codex_mcp_configured()
    config.write_text('[mcp_servers.ai-memory]\ncommand="test"\n')
    assert codex_mcp_configured()
    assert mcp_configured({"codex": {"available": True, "configured": True}})


@pytest.fixture
def desktop_server(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MEMORY_HOME", str(tmp_path / "memory"))
    monkeypatch.setattr("aimemory.desktop.get_watcher_service_status", lambda: WatcherServiceStatus(False, False))
    Handler.state = DesktopState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


def test_desktop_assets_status_and_request_guards(desktop_server):
    client = HTTPConnection("127.0.0.1", desktop_server.server_port)
    for path in ["/", "/assets/desktop.css", "/assets/desktop.js", "/assets/lucide.min.js"]:
        client.request("GET", path)
        response = client.getresponse()
        assert response.status == 200
        assert len(response.read()) > 100
    with FileLock(str(Handler.state.service.paths.state / "sync.lock")):
        client.request("GET", "/api/status")
        response = client.getresponse()
        data = json.loads(response.read())
        assert data["health"]["state"] == "stopped"
        assert data["sync_active"] is True
        assert "archive_bytes" in data["storage"]
    client.request("POST", "/api/import", body="{}")
    response = client.getresponse()
    assert response.status == 403
    response.read()
    client.request("GET", "/api/status", headers={"Host": "attacker.example"})
    response = client.getresponse()
    assert response.status == 403
    response.read()
    client.request("GET", "/assets/../desktop.py")
    response = client.getresponse()
    assert response.status == 404
    response.read()
    client.close()


def test_desktop_assets_explain_multi_client_mcp_and_two_step_icloud():
    html = Path("src/aimemory/assets/desktop.html").read_text(encoding="utf-8")
    script = Path("src/aimemory/assets/desktop.js").read_text(encoding="utf-8")

    assert "MCP pour Codex et Claude" in html
    assert 'id="icloud-2fa-label"' in html
    assert "icloudAwaiting2FA" in script
    assert "isIcloud2FAError(data.job.message)" in script
    assert "revealIcloud2FA()" in script
    assert "Confirmer le code iCloud" in script
    assert "VS Code" in script
