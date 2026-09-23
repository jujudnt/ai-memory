import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aimemory.config import AppPaths
from aimemory.service import MemoryService
from aimemory.state import write_json
from aimemory.storage_usage import storage_usage
from aimemory.sync.cloud_sync import _iter_local_objects
from aimemory.watcher.service import WatcherService


def test_storage_counts_categories_in_one_walk(tmp_path, monkeypatch):
    import aimemory.storage_usage as usage
    files = {"archive/sources/test/a": 5, "archive/raw/b": 7, "archive/snapshots/c": 9,
             "archive/extra": 2, "db/memory.sqlite": 11, "db/memory.sqlite-wal": 3,
             "cache/test": 13, "logs/log": 17}
    for relative, size in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
    walk = Mock(wraps=usage.os.walk)
    monkeypatch.setattr(usage.os, "walk", walk)
    assert storage_usage(tmp_path) == dict(archive_bytes=23, normalized_bytes=5, raw_bytes=7,
                                         revisions_bytes=9, database_bytes=14, cache_bytes=13, total_bytes=67)
    walk.assert_called_once()


def test_storage_does_not_follow_symlinks(tmp_path):
    home = tmp_path / "memory"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data").write_bytes(b"private")
    try:
        (home / "link").symlink_to(outside, target_is_directory=True)
        (home / "file-link").symlink_to(outside / "data")
    except OSError:
        pytest.skip("Symlink creation not available")
    assert storage_usage(home)["total_bytes"] == 0


def test_storage_refresh_is_cached_for_a_minute(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MEMORY_HOME", str(tmp_path))
    service = MemoryService()
    clock = [100.0]
    monkeypatch.setattr("aimemory.service.time.monotonic", lambda: clock[0])
    measure = Mock(wraps=storage_usage)
    monkeypatch.setattr("aimemory.service.storage_usage", measure)
    service.status()
    clock[0] = 130
    service.status()
    assert measure.call_count == 1
    clock[0] = 161
    service.status()
    assert measure.call_count == 2


def test_unchanged_synced_sources_are_not_read_again(tmp_path, monkeypatch):
    source = tmp_path / "sources" / "codex" / "sessions" / "example.json.zst"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"compressed archive")
    cache = {}
    objects = list(_iter_local_objects(tmp_path, True, fingerprint_cache=cache))
    relative, snapshot = objects[0]
    snapshot.unlink()  # Retention already removed this confirmed copy.
    original = Path.read_bytes
    reads = []
    def read(path):
        reads.append(path)
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", read)
    assert list(_iter_local_objects(tmp_path, True, already_synced={relative}, fingerprint_cache=cache)) == []
    assert not reads
    source.write_bytes(b"changed archive")
    updated = list(_iter_local_objects(tmp_path, True, already_synced={relative}, fingerprint_cache=cache))
    assert len(updated) == 1
    assert source in reads
    assert hashlib.sha256(original(updated[0][1])).hexdigest() in updated[0][0]


def test_cache_does_not_prevent_copying_to_a_new_destination(tmp_path):
    source = tmp_path / "sources" / "codex" / "sessions" / "example.json.zst"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"archive")
    cache = {}
    relative, snapshot = list(_iter_local_objects(tmp_path, True, fingerprint_cache=cache))[0]
    snapshot.unlink()
    assert list(_iter_local_objects(tmp_path, True, already_synced=set(), fingerprint_cache=cache)) == [(relative, snapshot)]
    assert snapshot.read_bytes() == b"archive"


def test_sync_cooldown_starts_after_completion(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MEMORY_HOME", str(tmp_path))
    service = MemoryService()
    monkeypatch.setattr(service, "import_all", lambda **kw: SimpleNamespace(scanned=0, imported=0, skipped=0, errors=[]))
    monkeypatch.setattr("aimemory.watcher.service.compact_search_index", lambda paths: None)
    monkeypatch.setattr("aimemory.watcher.service.run_auto_cleanup", lambda paths: None)
    clock = [100.0]
    monkeypatch.setattr("aimemory.watcher.service.time.monotonic", lambda: clock[0])
    calls = []
    def sync():
        calls.append(True)
        clock[0] += 90  # Longer than the normal one-minute interval.
    monkeypatch.setattr(service, "sync_now", sync)
    class Thread:
        def __init__(self, target, **kw):
            self.target = target
        def start(self):
            self.target()
        def is_alive(self):
            return False
    monkeypatch.setattr("aimemory.watcher.service.threading.Thread", Thread)
    watcher = WatcherService(service)
    watcher.scan_once()
    clock[0] = 200
    watcher.scan_once()
    assert len(calls) == 1
    clock[0] = 250
    watcher.scan_once()
    assert len(calls) == 2
    write_json(service.paths.state / "sync-request.json", {})
    watcher.scan_once()  # An explicit user request still bypasses the cooldown.
    assert len(calls) == 3


@pytest.mark.parametrize("current", [0, 15])
def test_background_watcher_only_lowers_priority(tmp_path, monkeypatch, current):
    monkeypatch.setenv("AI_MEMORY_HOME", str(tmp_path))
    paths = AppPaths.from_env()
    paths.ensure()
    watcher = WatcherService(SimpleNamespace(paths=paths))
    monkeypatch.setattr("aimemory.watcher.service.os.PRIO_PROCESS", 0, raising=False)
    monkeypatch.setattr("aimemory.watcher.service.os.getpriority", lambda *args: current, raising=False)
    set_priority = Mock()
    monkeypatch.setattr("aimemory.watcher.service.os.setpriority", set_priority, raising=False)
    def stop(*args):
        raise KeyboardInterrupt
    monkeypatch.setattr(watcher, "scan_once", stop)
    with pytest.raises(KeyboardInterrupt):
        watcher.run_polling()
    assert set_priority.call_args.args[-1] == max(current, 10)
    set_priority.reset_mock()
    with pytest.raises(KeyboardInterrupt):
        watcher.run_polling(once=True)
    set_priority.assert_not_called()


def test_cached_source_is_rehashed_before_recreating_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "sources" / "codex" / "sessions" / "example.json.zst"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"old archive")
    cache = {}
    _, snapshot = list(_iter_local_objects(tmp_path, True, fingerprint_cache=cache))[0]
    snapshot.unlink()
    original = Path.read_bytes
    def changing_read(path):
        if path == source:
            source.write_bytes(b"new archive")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", changing_read)
    relative, snapshot = list(_iter_local_objects(tmp_path, True, fingerprint_cache=cache))[0]
    assert original(snapshot) == b"new archive"
    assert hashlib.sha256(b"new archive").hexdigest() in relative
