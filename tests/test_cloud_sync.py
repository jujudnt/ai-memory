import hashlib
import errno
from pathlib import Path
from types import SimpleNamespace

import pytest
from filelock import FileLock

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
    status = CloudSync(SimpleNamespace(paths=paths, lock=FileLock(str(paths.state / "operations.lock")))).run()
    assert status["status"] == "synced"
    assert status["transfers"] == status["object_count"] == 2


def test_bulk_uploads_are_batched_and_counted_after_completion(tmp_path, monkeypatch):
    paths = AppPaths(tmp_path, *(tmp_path / name for name in
                               ("archive", "db", "vectors", "cache", "logs", "state")))
    paths.ensure()
    write_json(paths.state / "cloud.json", {"provider": "icloud-online", "root": "AI-Memory"})
    for index in range(121):
        digest = f"{index:064x}"
        path = paths.archive / f"snapshots/thread-{index}/{digest}.json.zst"
        path.parent.mkdir(parents=True)
        path.write_bytes(str(index).encode())
    batches = []

    class Remote:
        def list(self, progress):
            return set()

        def upload_many(self, uploads):
            status = read_json(paths.state / "sync-status.json")
            assert status["transfers"] == sum(len(batch) for batch in batches)
            batches.append([relative for relative, _ in uploads])

        def download(self, relative, local_path):
            raise AssertionError("No downloads expected")

    monkeypatch.setattr("aimemory.sync.cloud_sync._remote_archive", lambda *args: Remote())
    status = CloudSync(SimpleNamespace(paths=paths, lock=FileLock(str(paths.state / "operations.lock")))).run()

    assert [len(batch) for batch in batches] == [50, 50, 21]
    assert status["status"] == "synced"
    assert status["transfers"] == status["object_count"] == 121


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
    status = CloudSync(SimpleNamespace(paths=paths)).run()
    assert status["status"] == "waiting_local_cloud"
    status = read_json(paths.state / "sync-status.json")
    assert status["status"] == "waiting_local_cloud"
    assert status["requires_action"] is False
    assert not status.get("last_success_at")


def test_icloud_partial_raw_chain_waits_instead_of_reporting_checksum_error(tmp_path, monkeypatch):
    paths = AppPaths(tmp_path, *(tmp_path / name for name in
                               ("archive", "db", "vectors", "cache", "logs", "state")))
    paths.ensure()
    remote_root = tmp_path.parent / "icloud"
    relative = "raw/thread/" + "a" * 64 + ".delta.json.gz"
    raw = remote_root / relative
    raw.parent.mkdir(parents=True)
    import gzip
    import json
    raw.write_bytes(gzip.compress(json.dumps({
        "parent": "raw/thread/" + "b" * 64 + ".delta.json.gz",
        "prefix_size": 1,
        "append": "YQ==",
    }).encode()))
    write_json(paths.state / "cloud.json", {"provider": "icloud-drive", "root": str(remote_root)})
    requested = []
    monkeypatch.setattr("aimemory.sync.cloud_sync._request_icloud_download", requested.append)

    status = CloudSync(SimpleNamespace(paths=paths)).run()

    assert status["status"] == "waiting_local_cloud"
    assert "pr\u00e9pare encore" in status["error"]
    assert status["requires_action"] is False
    assert remote_root / "raw/thread" in requested
    assert remote_root / ("raw/thread/" + "b" * 64 + ".delta.json.gz") in requested


def test_cloud_read_retries_busy_but_does_not_hide_permission_errors(tmp_path, monkeypatch):
    source = tmp_path / "snapshot"
    from aimemory.sync import cloud_sync
    original = cloud_sync._stream_copy_atomic
    calls = []
    source.write_bytes(b"downloaded")
    def copy(input_path, output_path):
        calls.append(input_path)
        if len(calls) == 1:
            raise OSError(errno.EAGAIN, "Resource deadlock avoided")
        return original(input_path, output_path)
    monkeypatch.setattr(cloud_sync, "_stream_copy_atomic", copy)
    monkeypatch.setattr("aimemory.sync.cloud_sync.time.sleep", lambda _: None)
    remote = LocalArchiveRemote(tmp_path)
    remote.download("snapshot", tmp_path / "incoming/copy")
    assert (tmp_path / "incoming/copy").read_bytes() == b"downloaded"
    assert calls == [source, source]
    def denied():
        raise PermissionError(errno.EACCES, "Permission denied")
    with pytest.raises(PermissionError):
        remote._io(denied, source)


def test_local_cloud_copy_streams_and_replaces_atomically(tmp_path, monkeypatch):
    source = tmp_path / "source"
    target = tmp_path / "nested" / "target"
    source.write_bytes(b"chunk" * 1024 * 1024)
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("copy must be streamed"))
    from aimemory.sync.cloud_sync import _stream_copy_atomic
    _stream_copy_atomic(source, target)
    with target.open("rb") as stream:
        assert stream.read(10) == b"chunkchunk"
    assert target.stat().st_size == source.stat().st_size
    assert not list(target.parent.glob(".*.partial"))


@pytest.mark.parametrize("code", [errno.EDEADLK, errno.EAGAIN, errno.EBUSY])
def test_busy_cloud_setup_waits_then_resumes_without_manual_reload(tmp_path, monkeypatch, code):
    from test_cloud_integrity import memory
    service = memory(tmp_path / "memory")
    write_json(service.paths.state / "cloud.json", {"provider": "icloud-drive", "root": str(tmp_path / "cloud")})
    from aimemory.sync import cloud_sync
    original = cloud_sync._remote_archive
    attempts = []
    def remote(*args):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError(code, "Resource deadlock avoided")
        return original(*args)
    monkeypatch.setattr(cloud_sync, "_remote_archive", remote)
    pending = service.sync_now()
    assert pending["status"] == "waiting_local_cloud"
    assert pending["requires_action"] is False
    assert "deadlock" not in pending["error"]
    assert pending["retry_after"]
    resumed = service.sync_now()
    assert resumed["status"] == "synced"
    assert resumed["error"] is None
    assert resumed["message"] is None


def test_waiting_cloud_retries_after_short_interval(tmp_path, monkeypatch):
    from test_cloud_integrity import memory
    from aimemory.watcher.service import WatcherService
    service = memory(tmp_path / "memory")
    watcher = WatcherService(service)
    watcher._last_sync = 80
    write_json(service.paths.state / "sync-status.json", {
        "status": "waiting_local_cloud", "retry_after": 90,
    })
    monkeypatch.setattr(service, "import_all", lambda **kw: SimpleNamespace(
        scanned=0, imported=0, skipped=0, errors=[]))
    monkeypatch.setattr("aimemory.watcher.service.time.monotonic", lambda: 100)
    monkeypatch.setattr("aimemory.watcher.service.time.time", lambda: 100)
    started = []
    class Thread:
        def __init__(self, **kw):
            pass
        def start(self):
            started.append(True)
    monkeypatch.setattr("aimemory.watcher.service.threading.Thread", Thread)
    watcher.scan_once()
    assert started == [True]
