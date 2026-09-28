from pathlib import Path
import json
import sys

import pytest

from aimemory.cloud.providers import provider_label, resolve_folder_root, validate_sync_root


@pytest.fixture(autouse=True)
def macos_provider_defaults(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


def test_known_provider_labels_are_human_readable():
    assert provider_label("icloud-drive") == "iCloud Drive du Mac"
    assert provider_label("onedrive") == "OneDrive du Mac"
    assert provider_label("dropbox") == "Dropbox du Mac"
    assert provider_label("icloud-online") == "iCloud Drive"
    assert provider_label("onedrive-online") == "OneDrive"
    assert provider_label("dropbox-online") == "Dropbox"
    assert provider_label("google-drive") == "Google Drive"


def test_resolve_cloud_folder_roots(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs").mkdir(parents=True)
    (tmp_path / "Library" / "CloudStorage" / "OneDrive - Optimize Matter").mkdir(parents=True)
    (tmp_path / "Library" / "CloudStorage" / "Dropbox").mkdir(parents=True)

    assert resolve_folder_root("icloud-drive") == (
        tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "AI-Memory"
    )
    assert resolve_folder_root("onedrive") == (
        tmp_path / "Library" / "CloudStorage" / "OneDrive - Optimize Matter" / "AI-Memory"
    )
    assert resolve_folder_root("dropbox") == (
        tmp_path / "Library" / "CloudStorage" / "Dropbox" / "AI-Memory"
    )
    assert resolve_folder_root("local-folder", str(tmp_path / "custom")) == tmp_path / "custom"


def test_missing_cloud_clients_raise_actionable_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    with pytest.raises(ValueError, match="iCloud Drive est introuvable"):
        resolve_folder_root("icloud-drive")
    with pytest.raises(ValueError, match="OneDrive est introuvable"):
        resolve_folder_root("onedrive")
    with pytest.raises(ValueError, match="Dropbox est introuvable"):
        resolve_folder_root("dropbox")


def test_sync_root_must_be_separate_from_ai_memory_home(tmp_path):
    home = tmp_path / "memory"
    home.mkdir()

    with pytest.raises(ValueError, match="séparé"):
        validate_sync_root(home / "nested", home)
    with pytest.raises(ValueError, match="séparé"):
        validate_sync_root(tmp_path, home)


@pytest.mark.parametrize("provider,relative", [("icloud-drive", "iCloudDrive"), ("icloud-drive", "iCloud Drive"), ("onedrive", "OneDrive"), ("dropbox", "Dropbox")])
def test_windows_cloud_folders(tmp_path, monkeypatch, provider, relative):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / relative).mkdir()
    assert resolve_folder_root(provider) == tmp_path / relative / "AI-Memory"
    assert "du PC" in provider_label(provider)


def test_windows_moved_cloud_accounts_are_detected_without_guessing(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.delenv(key, raising=False)
    personal, work = tmp_path / "personal cloud", tmp_path / "work cloud"
    personal.mkdir()
    work.mkdir()
    monkeypatch.setenv("OneDrive", str(work))
    monkeypatch.setenv("OneDriveCommercial", str(work))
    assert resolve_folder_root("onedrive") == work / "AI-Memory"
    monkeypatch.setenv("OneDriveConsumer", str(personal))
    with pytest.raises(ValueError, match="Plusieurs"):
        resolve_folder_root("onedrive")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    config = tmp_path / "appdata/Dropbox/info.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"business": {"path": str(work)}}))
    assert resolve_folder_root("dropbox") == work / "AI-Memory"
    config.write_text(json.dumps({"business": {"path": str(work)}, "personal": {"path": str(personal)}}))
    with pytest.raises(ValueError, match="Plusieurs"):
        resolve_folder_root("dropbox")
    with pytest.raises(ValueError, match="Dossier synchronisé"):
        resolve_folder_root("icloud-drive")
