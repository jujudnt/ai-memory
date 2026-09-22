import hashlib
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from aimemory.state import read_json, write_json
from aimemory.sync import cloud_sync
from aimemory.sync.cloud_sync import LocalArchiveRemote, prune_synced_local_copies
from test_cloud_integrity import memory, session


@pytest.mark.parametrize("overrides,error,uploaded", [
    ({}, None, True),
    ({"NSURLIsUbiquitousItemKey": False}, None, False),
    ({"NSURLUbiquitousItemIsUploadedKey": False}, None, False),
    ({"NSURLUbiquitousItemIsUploadingKey": True}, None, False),
    ({"NSURLUbiquitousItemIsUploadingKey": None}, None, False),
    ({"NSURLUbiquitousItemUploadingErrorKey": "upload failed"}, None, False),
    ({}, "metadata unavailable", False),
])
def test_native_confirmation_fails_closed(monkeypatch, overrides, error, uploaded):
    names = ["NSURLIsUbiquitousItemKey", "NSURLFileSizeKey", "NSURLUbiquitousItemIsUploadedKey",
             "NSURLUbiquitousItemIsUploadingKey", "NSURLUbiquitousItemUploadingErrorKey",
             "NSURLUbiquitousItemDownloadingStatusKey", "NSURLUbiquitousItemDownloadingStatusCurrent"]
    values = {"NSURLIsUbiquitousItemKey": True, "NSURLUbiquitousItemIsUploadedKey": True,
              "NSURLUbiquitousItemIsUploadingKey": False, "NSURLFileSizeKey": 7, **overrides}
    url = SimpleNamespace(resourceValuesForKeys_error_=lambda *_: (values, error))
    foundation = SimpleNamespace(**{name: name for name in names},
                                 NSURL=SimpleNamespace(fileURLWithPath_=lambda _: url))
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert bool(cloud_sync._icloud_upload_state(Path("/example")).get("uploaded")) is uploaded


def copy_pair(tmp_path):
    service = memory(tmp_path / "memory")
    relative = f"snapshots/thread/{hashlib.sha256(b'archive').hexdigest()}.json.zst"
    local = service.paths.archive / relative
    remote = LocalArchiveRemote(tmp_path / "cloud", icloud=True)
    for path in (local, remote.root / relative):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"archive")
    return service, remote, relative, local


@pytest.mark.parametrize("state", [{}, {"uploaded": False, "size": 7}, {"uploaded": True, "size": 6}])
def test_no_native_confirmation_or_wrong_size_keeps_local_copy(tmp_path, monkeypatch, state):
    service, remote, relative, local = copy_pair(tmp_path)
    monkeypatch.setattr(cloud_sync, "_icloud_upload_state", lambda _: state)
    result = prune_synced_local_copies(service.paths, {relative: True}, confirm_copy=remote.confirm_uploaded_copy)
    assert result["freed_bytes"] == 0
    assert local.read_bytes() == b"archive"


def test_resident_cloud_copy_must_match_and_remain_uploaded(tmp_path, monkeypatch):
    service, remote, relative, local = copy_pair(tmp_path)
    monkeypatch.setattr(cloud_sync, "_icloud_upload_state", lambda _: {"uploaded": True, "size": 7, "resident": True})
    (remote.root / relative).write_bytes(b"corrupt")
    assert not remote.confirm_uploaded_copy(relative, local)
    (remote.root / relative).write_bytes(b"archive")
    states = iter([{"uploaded": True, "size": 7, "resident": True}, {"uploaded": False}])
    monkeypatch.setattr(cloud_sync, "_icloud_upload_state", lambda _: next(states))
    assert not remote.confirm_uploaded_copy(relative, local)


def test_evicted_confirmed_copy_is_not_downloaded_and_unknown_is_kept(tmp_path, monkeypatch):
    service, remote, relative, local = copy_pair(tmp_path)
    monkeypatch.setattr(cloud_sync, "_icloud_upload_state", lambda _: {"uploaded": True, "size": 7, "resident": False})
    monkeypatch.setattr(Path, "open", lambda *_a, **_k: pytest.fail("Must not hydrate iCloud"))
    # Avoid the unrelated JSON status writer; this assertion is about confirmation.
    assert remote.confirm_uploaded_copy(relative, local)
    monkeypatch.undo()
    result = prune_synced_local_copies(service.paths, {relative: False}, confirm_copy=remote.confirm_uploaded_copy)
    assert local.is_file() and result["removed_count"] == 0


def test_confirmed_icloud_purges_local_only_and_does_not_recreate_snapshots(tmp_path, monkeypatch):
    service = memory(tmp_path / "memory")
    codex = tmp_path / "codex"
    session(codex)
    service.import_codex(codex)
    remote = tmp_path / "cloud"
    write_json(service.paths.state / "cloud.json", {"provider": "icloud-drive", "root": str(remote)})
    monkeypatch.setattr(cloud_sync, "_icloud_upload_state", lambda p: {"uploaded": True, "size": p.stat().st_size, "resident": True})
    result = service.sync_now()
    assert result["retention"]["freed_bytes"] > 0
    assert not list((service.paths.archive / "snapshots").rglob("*.json.*"))
    assert not list((service.paths.archive / "raw").rglob("*.gz"))
    assert list((remote / "snapshots").rglob("*.json.*"))
    assert list((remote / "raw").rglob("*.gz"))
    assert service.search("hello")
    assert service.audit_codex(codex)["ok"]
    objects = list(cloud_sync._iter_local_objects(service.paths.archive, include_current_sources=True,
                                                already_synced=set(read_json(service.paths.state / "synced-objects.json"))))
    assert not objects
    assert service.sync_now()["retention"]["freed_bytes"] == 0


@pytest.mark.parametrize("pull_only,migration", [(True, False), (False, True)])
def test_migration_never_purges_source_copies(tmp_path, monkeypatch, pull_only, migration):
    service, remote, relative, local = copy_pair(tmp_path)
    write_json(service.paths.state / "cloud.json", {"provider": "icloud-drive", "root": str(remote.root)})
    write_json(service.paths.state / "synced-objects.json", {relative: True})
    if migration:
        write_json(service.paths.state / "migration.json", {"source_identity": cloud_sync._remote_identity(read_json(service.paths.state / "cloud.json")), "objects": [relative]})
    monkeypatch.setattr(cloud_sync, "_icloud_upload_state", lambda p: pytest.fail("No retention during migration"))
    service.pull_cloud_now() if pull_only else service.sync_now()
    assert local.is_file()


def test_known_snapshots_are_reclaimed_before_unrelated_download_fails(tmp_path, monkeypatch):
    service, remote, relative, local = copy_pair(tmp_path)
    write_json(service.paths.state / "cloud.json", {"provider": "icloud-drive", "root": str(remote.root)})
    write_json(service.paths.state / "synced-objects.json", {relative: True})
    unknown = remote.root / ("snapshots/other/" + "b" * 64 + ".json.zst")
    unknown.parent.mkdir(parents=True)
    unknown.write_bytes(b"bad")
    monkeypatch.setattr(cloud_sync, "_icloud_upload_state", lambda p: {"uploaded": True, "size": p.stat().st_size, "resident": True})
    assert service.sync_now()["status"] == "waiting_local_cloud"
    assert not local.exists()
    assert (remote.root / relative).exists()
    assert read_json(service.paths.state / "retention-status.json")["freed_bytes"] == 7


def test_symlinked_copy_is_never_removed(tmp_path, monkeypatch):
    service, remote, relative, local = copy_pair(tmp_path)
    outside = tmp_path / "outside"
    local.rename(outside)
    try:
        local.symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks unavailable")
    result = prune_synced_local_copies(service.paths, {relative: True}, confirm_copy=lambda *_: True)
    assert outside.read_bytes() == b"archive"
    assert local.is_symlink() and result["removed_count"] == 0
