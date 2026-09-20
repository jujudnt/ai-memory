import hashlib
from types import SimpleNamespace

from aimemory.config import AppPaths
from aimemory.state import read_json, write_json
from aimemory.sync.cloud_sync import CloudSync, _iter_local_objects


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
