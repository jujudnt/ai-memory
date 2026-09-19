import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from aimemory.cloud.google_drive import GoogleDriveProvider, _friendly_google_error, rclone_binary
from aimemory.cloud.rclone_provider import RcloneCloudProvider, _friendly_rclone_error
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


def test_icloud_auth_errors_explain_2fa_code():
    message = _friendly_rclone_error("icloud-online", "unauthorized: two-factor verification required", 1)

    assert "code 2FA" in message
    assert "pas un mot de passe spécifique d'app" in message


def test_icloud_connect_continues_the_same_apple_session(tmp_path, monkeypatch):
    provider = RcloneCloudProvider.for_provider(paths(tmp_path), "icloud-online")
    calls = []

    def run(args, timeout=600, config=None):
        calls.append((args, config))
        if args[0] == "obscure":
            return "obscured-password"
        if args[:2] == ["config", "create"]:
            config.write_text(
                "[aimemory-icloud]\n"
                "type = iclouddrive\n"
                "apple_id = julia@example.com\n"
                "password = obscured-password\n"
                "_auth_session = preserved-session\n"
                "cookies = first-cookie\n",
                encoding="utf-8",
            )
            return json.dumps(
                {
                    "State": "2fa_do",
                    "Option": {"Name": "config_2fa", "Help": "Enter the verification code"},
                    "Error": "",
                    "Result": "",
                }
            )
        if args[:2] == ["config", "update"]:
            assert "_auth_session = preserved-session" in config.read_text(encoding="utf-8")
            config.write_text(
                "[aimemory-icloud]\n"
                "type = iclouddrive\n"
                "apple_id = julia@example.com\n"
                "password = obscured-password\n"
                "cookies = trusted-cookie\n"
                "trust_token = trusted-token\n",
                encoding="utf-8",
            )
            return json.dumps({"State": "", "Option": None, "Error": "", "Result": ""})
        return ""

    monkeypatch.setattr(provider, "run", run)

    first = provider.connect({"apple_id": "julia@example.com", "password": "secret"})

    assert first["status"] == "needs_2fa"
    assert provider.icloud_pending_config.exists()
    assert provider.icloud_auth_state_path.exists()
    create_call = next(args for args, _ in calls if args[:2] == ["config", "create"])
    assert "--non-interactive" in create_call
    assert "config_2fa" not in create_call

    second = provider.connect({"two_factor_code": "123456"})

    assert second["status"] == "connected"
    update_call = next(args for args, _ in calls if args[:2] == ["config", "update"])
    assert update_call[update_call.index("--state") + 1] == "2fa_do"
    assert update_call[update_call.index("--result") + 1] == "123456"
    assert "apple_id" not in update_call
    assert "password" not in update_call
    assert provider.config.exists()
    assert not provider.icloud_pending_config.exists()
    assert not provider.icloud_auth_state_path.exists()


@pytest.mark.parametrize(
    ("name", "options", "expected"),
    [
        ("dropbox-online", {}, ["config_is_local", "true"]),
        (
            "onedrive-online",
            {"onedrive_type": "business"},
            ["config_is_local", "true", "region", "global", "drive_type", "business"],
        ),
    ],
)
def test_browser_cloud_connectors_create_a_tokenized_remote(tmp_path, monkeypatch, name, options, expected):
    provider = RcloneCloudProvider.for_provider(paths(tmp_path / name), name)
    calls = []

    def run(args, timeout=600, config=None):
        calls.append(args)
        if args[:2] == ["config", "create"]:
            config.write_text(
                f"[{provider.spec.remote_name}]\n"
                f"type = {provider.spec.backend}\n"
                'token = {"access_token":"private"}\n',
                encoding="utf-8",
            )
        return ""

    monkeypatch.setattr(provider, "run", run)
    provider.connect(options)

    create_call = next(call for call in calls if call[:2] == ["config", "create"])
    for index in range(0, len(expected), 2):
        key = expected[index]
        assert create_call[create_call.index(key) + 1] == expected[index + 1]
    assert "private" not in str(read_json(provider.paths.state / "cloud.json"))
    assert provider.config.stat().st_mode & 0o777 == 0o600


def test_icloud_code_without_a_pending_session_is_rejected(tmp_path):
    provider = RcloneCloudProvider.for_provider(paths(tmp_path), "icloud-online")

    with pytest.raises(RuntimeError, match="session Apple"):
        provider.connect({"two_factor_code": "123456"})
