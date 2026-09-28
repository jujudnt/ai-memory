import json
import plistlib
import socket
import threading
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from filelock import FileLock

from aimemory.desktop import DesktopState, Handler, _create_desktop_server, codex_mcp_configured, mcp_configured
from aimemory.state import write_json
from aimemory.health import watcher_health
from aimemory.installer import WatcherServiceStatus
from aimemory.menubar import ensure_login, login_path, set_login_enabled, status_icon_available, _windows_login_command


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


def test_windows_status_icon_uses_hidden_desktop_executable(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", r"C:\Program Files\AI Memory\AI Memory.exe")

    assert status_icon_available()
    assert _windows_login_command() == r'"C:\Program Files\AI Memory\AI Memory.exe" --background'


def test_desktop_reuses_previous_port_and_falls_back_when_busy(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    write_json(state / "desktop.json", {"port": port})
    server = _create_desktop_server(state)
    try:
        assert server.server_port == port
    finally:
        server.server_close()

    occupied = socket.socket()
    occupied.bind(("127.0.0.1", port))
    occupied.listen()
    try:
        server = _create_desktop_server(state)
        try:
            assert server.server_port != port
        finally:
            server.server_close()
    finally:
        occupied.close()


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


def test_windows_manual_watcher_is_hidden_and_keeps_logs(desktop_server, monkeypatch):
    from unittest.mock import Mock
    import subprocess
    monkeypatch.setattr("aimemory.desktop.background_creationflags", lambda: 0x08000000)
    monkeypatch.setattr(Handler.state.service, "watcher_status", lambda: {})
    process = Mock()
    process.poll.return_value = None
    spawn = Mock(return_value=process)
    monkeypatch.setattr("aimemory.desktop.subprocess.Popen", spawn)
    client = HTTPConnection("127.0.0.1", desktop_server.server_port)
    try:
        for _ in range(2):
            client.request("POST", "/api/start-watcher", body="{}",
                           headers={"X-AI-Memory-Token": Handler.state.token})
            response = client.getresponse()
            assert response.status == 200, response.read()
            response.read()
        spawn.assert_called_once()
        options = spawn.call_args.kwargs
        assert options["creationflags"] == 0x08000000
        assert options["stdin"] == subprocess.DEVNULL
        assert options["stdout"].closed and options["stderr"].closed
        assert Path(options["stderr"].name).name == "watcher.err.log"
    finally:
        client.close()


def test_theme_preference_is_stored_and_reported(desktop_server):
    client = HTTPConnection("127.0.0.1", desktop_server.server_port)
    headers = {"X-AI-Memory-Token": Handler.state.token}
    try:
        client.request("GET", "/api/status", headers=headers)
        response = client.getresponse()
        assert json.loads(response.read())["theme"] == "system"

        client.request("POST", "/api/theme", body=json.dumps({"theme": "dark"}), headers=headers)
        response = client.getresponse()
        assert response.status == 200, response.read()
        response.read()

        client.request("GET", "/api/status", headers=headers)
        response = client.getresponse()
        assert json.loads(response.read())["theme"] == "dark"

        client.request("POST", "/api/theme", body=json.dumps({"theme": "sepia"}), headers=headers)
        response = client.getresponse()
        assert response.status == 400
        response.read()

        client.request("GET", "/api/status", headers=headers)
        response = client.getresponse()
        assert json.loads(response.read())["theme"] == "dark"
    finally:
        client.close()


def test_projects_search_and_bundled_fonts(desktop_server, tmp_path):
    sessions = tmp_path / "codex" / "sessions"
    sessions.mkdir(parents=True)
    for name, cwd, text, stamp in [("a", "alpha", "retrouver la migration iCloud", "2026-09-20T10:00:00Z"),
                                   ("b", "alpha", "corriger le tableau", "2026-09-22T10:00:00Z"),
                                   ("c", "beta", "preparer la synthese", "2026-09-21T10:00:00Z")]:
        (tmp_path / cwd).mkdir(exist_ok=True)
        records = [{"type": "session_meta", "payload": {"id": name, "cwd": str(tmp_path / cwd)}},
                   {"type": "response_item", "timestamp": stamp,
                    "payload": {"type": "message", "role": "user", "content": text}}]
        (sessions / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    assert Handler.state.service.import_codex(tmp_path / "codex").imported == 3
    client = HTTPConnection("127.0.0.1", desktop_server.server_port)

    def get(path):
        client.request("GET", path)
        response = client.getresponse()
        assert response.status == 200, path
        return response.read()

    try:
        projects = {p["name"]: p for p in json.loads(get("/api/projects"))["projects"]}
        assert projects["alpha"]["conversation_count"] == 2
        assert projects["alpha"]["latest_conversation_at"].startswith("2026-09-22")
        rows = json.loads(get(f"/api/conversations?project={projects['alpha']['id']}"))["conversations"]
        assert len(rows) == 2 and {r["project_name"] for r in rows} == {"alpha"}
        rows = json.loads(get("/api/conversations?q=migration&limit=abc"))["conversations"]
        assert [r["latest_user_message"] for r in rows] == ["retrouver la migration iCloud"]
        assert json.loads(get("/api/conversations?q=%22"))["conversations"] == []
        first = json.loads(get("/api/conversations?limit=2"))
        second = json.loads(get("/api/conversations?limit=2&offset=2"))
        assert first["has_more"] is True and first["next_offset"] == 2
        assert second["has_more"] is False
        assert len({row["id"] for row in first["conversations"] + second["conversations"]}) == 3
        devices = json.loads(get("/api/devices"))["devices"]
        assert len(devices) == 1 and devices[0]["is_current"]
        assert not json.loads(get("/api/conversations?device=other"))["conversations"]
        assert len(json.loads(get("/api/conversations?device=current&source=codex"))["conversations"]) == 3
        page = json.loads(get(f"/api/conversation?id={first['conversations'][0]['id']}"))["conversation"]
        assert page["device_name"] == devices[0]["name"] and page["messages"]
        assert get("/assets/fonts/archivo.woff2")[:4] == b"wOF2"
        assert get("/assets/fonts/oswald.woff2")[:4] == b"wOF2"
        assert b'data-platform="__PLATFORM__"' not in get("/")
    finally:
        client.close()


def test_folder_picked_in_local_mirror_becomes_cloud_path(tmp_path):
    from aimemory.folder_picker import cloud_relative_folder
    root = tmp_path / "iCloud"
    (root / "Sauvegardes" / "AI").mkdir(parents=True)
    assert cloud_relative_folder(root / "Sauvegardes" / "AI", [root], "icloud-online") == "Sauvegardes/AI"
    for outside in (root, tmp_path):
        with pytest.raises(ValueError):
            cloud_relative_folder(outside, [root], "icloud-online")


def test_choose_folder_endpoint(desktop_server, tmp_path, monkeypatch):
    from aimemory.state import write_json
    mirror = tmp_path / "iCloud"
    (mirror / "Sauvegardes").mkdir(parents=True)
    calls = []
    picked = {"value": mirror / "Sauvegardes"}
    monkeypatch.setattr("aimemory.folder_picker.choose_folder",
                        lambda prompt, start=None: calls.append(start) or picked["value"])
    monkeypatch.setattr("aimemory.folder_picker.cloud_sync_roots", lambda provider: [mirror])
    monkeypatch.setattr("aimemory.folder_picker.picker_supported", lambda: True)
    headers = {"X-AI-Memory-Token": Handler.state.token}
    client = HTTPConnection("127.0.0.1", desktop_server.server_port)

    def post(body):
        client.request("POST", "/api/choose-folder", body=json.dumps(body), headers=headers)
        response = client.getresponse()
        return response.status, json.loads(response.read())

    try:
        assert post({"purpose": "backup"})[0] == 400  # no destination yet
        write_json(Handler.state.service.paths.state / "cloud.json", {"provider": "icloud-online", "root": "AI-Memory"})
        client.request("GET", "/api/status")
        assert json.loads(client.getresponse().read())["backup_folder_picker"] is True
        assert post({"purpose": "backup"}) == (200, {"path": "Sauvegardes"})
        assert calls[-1] == mirror  # AI-Memory does not exist locally, so the dialog opens at the root
        picked["value"] = None
        assert post({"purpose": "backup"}) == (200, {"path": None})  # cancelled
        picked["value"] = tmp_path / "ailleurs"
        assert post({"purpose": "connect"}) == (200, {"path": str(tmp_path / "ailleurs")})
    finally:
        client.close()


def test_desktop_assets_explain_multi_client_mcp_and_two_step_icloud():
    html = Path("src/aimemory/assets/desktop.html").read_text(encoding="utf-8")
    script = Path("src/aimemory/assets/desktop.js").read_text(encoding="utf-8")

    assert "MCP pour Codex et Claude" in html
    assert 'id="icloud-credentials"' in html
    assert 'id="icloud-2fa-label"' in html
    assert 'id="icloud-web-approval"' in html
    assert 'id="icloud-terms"' in html
    assert "https://www.icloud.com/" in html
    assert "icloudAwaiting2FA" in script
    assert "icloudAwaitingWebApproval" in script
    assert "icloudAwaitingTerms" in script
    assert 'data.icloud_auth?.status === "needs_2fa"' in script
    assert '["needs_web_approval", "needs_access_retry"]' in script
    assert 'data.icloud_auth?.status === "needs_terms_acceptance"' in script
    assert "revealIcloud2FA()" in script
    assert '$("#icloud-credentials").hidden' in script
    assert "Confirmer le code iCloud" in script
    assert "R\\u00e9essayer iCloud" in script
    assert "J'ai accept\\u00e9, relancer" in script
    assert "VS Code" in script


def test_reconnect_bypasses_expired_old_session_and_preserves_identity(desktop_server, monkeypatch):
    from aimemory.state import read_json, write_json
    state = Handler.state
    config_path = state.service.paths.state / "cloud.json"
    write_json(config_path, {"provider": "google-drive", "root": "Backups", "connection_id": "same-account"})
    monkeypatch.setattr(state, "start_job", lambda name, operation: operation())
    monkeypatch.setattr("aimemory.desktop._pull_current_destination", lambda service: pytest.fail("Expired account must not be downloaded first"))
    monkeypatch.setattr("aimemory.desktop.GoogleDriveProvider.connect", lambda *args, **kwargs:
                        write_json(config_path, {"provider": "google-drive", "root": "Backups", "connection_id": "renewed"}))
    monkeypatch.setattr(state.service, "sync_now", lambda: {"status": "synced"})
    client = HTTPConnection("127.0.0.1", desktop_server.server_port)
    client.request("POST", "/api/connect-cloud", body=json.dumps({"provider": "google-drive", "reauthenticate": True}),
                   headers={"X-AI-Memory-Token": state.token})
    response = client.getresponse()
    assert response.status == 200, response.read()
    response.read()
    client.close()
    assert read_json(config_path)["connection_id"] == "same-account"


def test_pause_does_not_wait_for_desktop_job(desktop_server):
    from aimemory.state import read_json
    state = Handler.state
    state.job = {"running": True}
    client = HTTPConnection("127.0.0.1", desktop_server.server_port)
    client.request("POST", "/api/pause-sync", body="{}", headers={"X-AI-Memory-Token": state.token})
    response = client.getresponse()
    assert response.status == 200
    response.read()
    client.close()
    assert read_json(state.service.paths.state / "sync-preferences.json")["paused"]
    assert (state.service.paths.state / "sync-cancel.json").exists()
