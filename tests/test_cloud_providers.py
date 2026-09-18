from pathlib import Path

import pytest

from aimemory.cloud.providers import provider_label, resolve_folder_root, validate_sync_root


def test_known_provider_labels_are_human_readable():
    assert provider_label("icloud-drive") == "iCloud Drive"
    assert provider_label("onedrive") == "OneDrive"
    assert provider_label("dropbox") == "Dropbox"
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
