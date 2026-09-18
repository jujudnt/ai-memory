import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from aimemory.cloud.google_drive import GoogleDriveProvider, _friendly_google_error, rclone_binary
from aimemory.config import AppPaths
from aimemory.state import read_json


def paths(root):
    result = AppPaths(root, *(root / name for name in ("archive", "db", "vectors", "cache", "logs", "state")))
    result.ensure()
    return result


def test_browser_oauth_uses_restricted_scope_and_keeps_token_private(tmp_path, monkeypatch):
    provider = GoogleDriveProvider(paths(tmp_path))
    calls = []
    def run(args, timeout=600, config=None):
        calls.append(args)
        if args[0] == "config":
            config.write_text('[aimemory]\ntype = drive\ntoken = {"access_token":"private"}\n')
        return ""
    monkeypatch.setattr(provider, "run", run)
    provider.connect()
    assert "drive.file" in calls[0]
    assert "config_is_local" in calls[0]
    assert "private" not in str(read_json(provider.paths.state / "cloud.json"))
    assert provider.config.exists()
    provider.disconnect()
    assert not provider.config.exists()
    assert not (provider.paths.state / "cloud.json").exists()


def test_real_rclone_exchange_reports_progress(tmp_path):
    try:
        rclone_binary()
    except RuntimeError:
        pytest.skip("Bundled rclone not downloaded")
    provider = GoogleDriveProvider(paths(tmp_path / "home"))
    provider.remote = str(tmp_path / "remote")
    local = tmp_path / "local"
    local.mkdir()
    (local / "a.txt").write_text("local file")
    remote = Path(provider.remote)
    remote.mkdir()
    (remote / "b.txt").write_text("remote file")
    phases = []
    provider.exchange(local, lambda phase, **stats: phases.append((phase, stats)))
    assert (local / "b.txt").read_text() == "remote file"
    assert (remote / "a.txt").read_text() == "local file"
    assert {phase for phase, stats in phases} == {"uploading", "downloading"}
    assert any(stats.get("bytes", 0) > 0 for phase, stats in phases)


def test_google_quota_errors_are_actionable():
    message = _friendly_google_error("storageQuotaExceeded: The user's Drive storage quota has been exceeded", 1)

    assert "Google Drive est plein" in message
    assert "iCloud" in message
