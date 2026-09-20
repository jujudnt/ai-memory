import hashlib
import errno
from pathlib import Path
from types import SimpleNamespace

import pytest

from aimemory.config import AppPaths
from aimemory.state import read_json, write_json
from aimemory.sync.cloud_sync import CloudSync, CloudFolderPending, LocalArchiveRemote, _iter_local_objects


def test_current_conversations_precede_history_and_are_immutable(tmp_path):
    source = tmp_path / "sources/codex/sessions/b.json.zst"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"current")
    old = tmp_path / ("snapshots/a/" + "a" * 64 + ".json.zst")
    old.parent.mkdir(parents=True)
    old.write_bytes(b"history")

    objects = list(_iter_local_objects(tmp_path, include_current_sources=True))

    assert len(objects) == 2
    relative, snapshot = objects[0]
    assert relative == f"snapshots/b/{hashlib.sha256(b'current').hexdigest()}.json.zst"
    assert objects[1][1] == old
    source.write_bytes(b"changed during sync")
    assert snapshot.read_bytes() == b"current"
    assert len(list(_iter_local_objects(tmp_path))) == 2


def test_upload_counter_counts_only_completed_files(tmp_path, monkeypatch):
    paths = AppPaths(tmp_path, *(tmp_path / name for name in
                               ("archive", "db", "vectors", "cache", "logs", "state")))
    paths.ensure()
    write_json(paths.state / "cloud.json", {"provider": "icloud-online", "root": "AI-Memory"})
    for name in ("a", "b"):
        path = paths.archive / f"snapshots/{name}/{name * 64}.json.zst"
        path.parent.mkdir(parents=True)
        path.write_bytes(name.encode())
    uploaded = []

    class Remote:
        def list(self, progress):
            return set()

        def upload(self, path, relative):
            status = read_json(paths.state / "sync-status.json")
            assert status["transfers"] == len(uploaded)
            assert status["object_count"] == len(uploaded)
            uploaded.append(relative)

    monkeypatch.setattr("aimemory.sync.cloud_sync._remote_archive", lambda *args: Remote())
    status = CloudSync(SimpleNamespace(paths=paths)).run()
    assert status["status"] == "synced"
    assert status["transfers"] == status["object_count"] == 2


def test_icloud_retries_inaccessible_subfolder_without_omitting_files(tmp_path, monkeypatch):
    path = tmp_path / "snapshots/thread" / ("a" * 64 + ".json.zst")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"snapshot")
    original = Path.iterdir
    attempts = []
    requests = []
    def iterdir(directory):
        if directory == path.parent:
            attempts.append(directory)
            if len(attempts) < 3:
                raise OSError(errno.EDEADLK, "Resource deadlock avoided", str(directory))
        return original(directory)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    monkeypatch.setattr("aimemory.sync.cloud_sync.time.sleep", lambda _: None)
    monkeypatch.setattr("aimemory.sync.cloud_sync._request_icloud_download", requests.append)
    phases = []
    result = LocalArchiveRemote(tmp_path, icloud=True).list(lambda phase, **kw: phases.append(phase))
    assert result == {path.relative_to(tmp_path).as_posix()}
    assert len(attempts) == 3
    assert requests == [path.parent, path.parent]
    assert "waiting_local_cloud" in phases


def test_pending_folder_prevents_success_and_local_cleanup(tmp_path, monkeypatch):
    paths = AppPaths(tmp_path, *(tmp_path / name for name in
                               ("archive", "db", "vectors", "cache", "logs", "state")))
    paths.ensure()
    write_json(paths.state / "cloud.json", {"provider": "icloud-drive", "root": str(tmp_path / "remote")})
    remote = LocalArchiveRemote(tmp_path / "remote", icloud=True)
    def blocked(_):
        raise OSError(errno.EDEADLK, "Resource deadlock avoided")
    monkeypatch.setattr(Path, "iterdir", blocked)
    monkeypatch.setattr("aimemory.sync.cloud_sync.time.sleep", lambda _: None)
    monkeypatch.setattr("aimemory.sync.cloud_sync._request_icloud_download", lambda _: None)
    monkeypatch.setattr("aimemory.sync.cloud_sync._remote_archive", lambda *args: remote)
    def unexpected_cleanup(*args):
        pytest.fail("Must not prune after an incomplete cloud listing")
    monkeypatch.setattr("aimemory.sync.cloud_sync.prune_synced_local_copies", unexpected_cleanup)
    with pytest.raises(CloudFolderPending):
        CloudSync(SimpleNamespace(paths=paths)).run()
    status = read_json(paths.state / "sync-status.json")
    assert status["status"] == "waiting_local_cloud"
    assert status["requires_action"] is False
    assert not status.get("last_success_at")


def test_cloud_read_retries_busy_but_does_not_hide_permission_errors(tmp_path, monkeypatch):
    source = tmp_path / "snapshot"
    original = Path.read_bytes
    calls = []
    source.write_bytes(b"downloaded")
    def read(path):
        if path == source:
            calls.append(path)
            if len(calls) == 1:
                raise OSError(errno.EAGAIN, "Resource deadlock avoided")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr("aimemory.sync.cloud_sync.time.sleep", lambda _: None)
    remote = LocalArchiveRemote(tmp_path)
    remote.download("snapshot", tmp_path / "incoming/copy")
    assert (tmp_path / "incoming/copy").read_bytes() == b"downloaded"
    def denied():
        raise PermissionError(errno.EACCES, "Permission denied")
    with pytest.raises(PermissionError):
        remote._io(denied, source)
