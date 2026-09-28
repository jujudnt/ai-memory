from types import SimpleNamespace

import pytest

from aimemory.cloud.errors import CloudError, error_category
from aimemory.cloud.rclone_provider import RcloneCloudProvider, _friendly_rclone_error
from aimemory.state import read_json, write_json
from aimemory.watcher.service import WatcherService
from test_cloud_integrity import memory


@pytest.mark.parametrize("output", [
    "401 Unauthorized", "403 Forbidden", "authentication failed: HTTP 503",
    "authStart: unexpected status 403 Forbidden",
    "requestPCS(iclouddrive): context deadline exceeded",
    "validate2FACode failed: connection timed out",
])
def test_temporary_icloud_failures_do_not_request_reconnection(output):
    assert error_category(output, "icloud-online") == "transient"
    message = _friendly_rclone_error("icloud-online", output, 1)
    assert "automatique" in message
    assert "expiré" not in message and "reconnect" not in message.lower()


@pytest.mark.parametrize("output", [
    "trust token expired, please reauth",
    "missing icloud trust token: try refreshing it",
    "sign in failed: incorrect username or password",
    "unauthorized: two-factor verification required",
    "validate2FACode failed: 400 Bad Request",
    "requestPCS(iclouddrive): Missing PCS cookies from the request",
    "requestPCS(iclouddrive): timed out waiting for device approval after 5 minutes",
    "termsUpdateNeeded",
])
def test_explicit_apple_challenges_remain_actionable(output):
    assert error_category(output, "icloud-online") == "auth"


def test_quota_and_other_provider_authentication_keep_their_classification():
    assert error_category("403 Forbidden: storage quota exceeded", "icloud-online") == "quota"
    assert error_category("401 Unauthorized", "onedrive-online") == "auth"
    assert error_category("invalid_grant", "google-drive") == "auth"


def test_connection_check_does_not_turn_pcs_network_failure_into_approval_request(tmp_path, monkeypatch):
    provider = RcloneCloudProvider.for_provider(memory(tmp_path).paths, "icloud-online")
    monkeypatch.setattr("aimemory.cloud.rclone_provider.rclone_binary", lambda: "rclone")
    monkeypatch.setattr("aimemory.cloud.rclone_provider.subprocess.run", lambda *a, **kw:
                        SimpleNamespace(returncode=1, stdout="", stderr="requestPCS(iclouddrive): connection timed out"))
    with pytest.raises(CloudError) as error:
        provider.run(["lsjson", provider.spec.remote])
    assert error.value.category == "transient"


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    service = memory(tmp_path)
    write_json(service.paths.state / "cloud.json", {"provider": "icloud-online", "root": "AI-Memory"})
    clock = [1000.0]
    monkeypatch.setattr("aimemory.watcher.service.time.time", lambda: clock[0])
    monkeypatch.setattr("aimemory.watcher.service.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(service, "import_all", lambda **kw: SimpleNamespace(scanned=0, imported=0, skipped=0, errors=[]))
    monkeypatch.setattr("aimemory.watcher.service.compact_search_index", lambda paths: None)
    monkeypatch.setattr("aimemory.watcher.service.run_auto_cleanup", lambda paths: None)

    class Thread:
        def __init__(self, target, **kw):
            self.target = target
        def start(self):
            self.target()
        def is_alive(self):
            return False

    monkeypatch.setattr("aimemory.watcher.service.threading.Thread", Thread)
    monkeypatch.setattr("aimemory.sync.cloud_sync.rclone_binary", lambda: "rclone")
    errors = []
    calls = []

    class Process:
        def __init__(self, args, **kw):
            calls.append(args)
            self.error = errors.pop(0) if errors else ""
            self.returncode = int(bool(self.error))
        def communicate(self, **kw):
            return "[]", self.error
        def poll(self):
            return self.returncode

    monkeypatch.setattr("aimemory.sync.cloud_sync.subprocess.Popen", Process)
    return SimpleNamespace(service=service, watcher=WatcherService(service), clock=clock,
                           errors=errors, calls=calls, status=service.paths.state / "sync-status.json")


def test_watcher_recovers_without_reload_or_new_login(recovery):
    r = recovery
    r.errors.append("401 Unauthorized")
    r.watcher.scan_once()
    failure = read_json(r.status)
    assert failure["status"] == "error" and not failure["requires_action"]
    assert failure["retry_after"] > r.clock[0]
    r.clock[0] = failure["retry_after"] - 1
    r.watcher.scan_once()
    assert len(r.calls) == 1
    r.clock[0] += 1
    r.watcher.scan_once()
    success = read_json(r.status)
    assert success["status"] == "synced"
    assert success["error"] is None and success["failed_at"] is None
    assert success["retry_after"] is None and success["failures"] == 0
    assert len(r.calls) == 2
    assert all("lsjson" in call and "reconnect" not in call and "update" not in call for call in r.calls)


def test_repeated_temporary_failures_back_off_but_do_not_disable_sync(recovery):
    r = recovery
    r.errors.extend(["403 Forbidden"] * 7)
    delays = []
    for _ in range(7):
        r.watcher.scan_once()
        status = read_json(r.status)
        assert not status["requires_action"]
        delays.append(status["retry_after"] - r.clock[0])
        r.clock[0] = status["retry_after"]
    assert delays == sorted(delays) and delays[-1] == 900
    r.watcher.scan_once()
    assert read_json(r.status)["status"] == "synced"


def test_real_2fa_requirement_does_not_keep_requesting_codes(recovery):
    r = recovery
    r.errors.append("trust token expired, please reauth")
    r.watcher.scan_once()
    assert read_json(r.status)["auth_action_confirmed"] is True
    r.clock[0] += 3600
    r.watcher.scan_once()
    assert len(r.calls) == 1
    assert read_json(r.status)["requires_action"] is True


@pytest.mark.parametrize("pause,pending,provider,expected_calls", [
    (False, False, "icloud-online", 1),
    (True, False, "icloud-online", 0),
    (False, True, "icloud-online", 0),
    (False, False, "google-drive", 0),
])
def test_legacy_icloud_block_is_rechecked_without_overriding_user_actions(recovery, pause, pending, provider, expected_calls):
    r = recovery
    state = r.service.paths.state
    write_json(state / "cloud.json", {"provider": provider, "root": "AI-Memory"})
    write_json(r.status, {"status": "error", "error_category": "auth", "requires_action": True, "retry_after": 900})
    write_json(state / "sync-preferences.json", {"paused": pause})
    if pending:
        write_json(state / "icloud-auth.json", {"status": "needs_2fa"})
    r.watcher.scan_once()
    assert len(r.calls) == expected_calls
    if expected_calls:
        assert read_json(r.status)["status"] == "synced"
