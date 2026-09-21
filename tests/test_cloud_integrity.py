import gzip
import json
from dataclasses import replace

import pytest

from aimemory.config import AppPaths
from aimemory.service import MemoryService
from aimemory.state import write_json


def memory(root):
    return MemoryService(AppPaths(root, *(root / name for name in ("archive", "db", "vectors", "cache", "logs", "state"))))


def session(root, name="child", text="hello", timestamp="2026-09-18T10:00:00Z"):
    path = root / "sessions" / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "session_meta", "payload": {"id": name, "session_id": "shared-parent"}},
        {"type": "response_item", "timestamp": timestamp, "payload": {"type": "message", "role": "user", "content": text}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "call_id": "c1", "name": "patch", "input": "test input"}},
        {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "c1", "output": "done"}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def test_forks_are_distinct_and_raw_backup_is_exact(tmp_path):
    codex = tmp_path / "codex"
    session(codex, "a")
    session(codex, "b")
    service = memory(tmp_path / "memory")
    result = service.import_codex(codex)
    assert result.imported == 2 and not result.errors
    assert service.status()["conversation_count"] == 2
    assert service.audit_codex(codex)["ok"]
    assert service.import_codex(codex).skipped == 2
    assert service.get_conversation(service.list_conversations()[0]["id"]).tool_calls[0].output == "done"
    backup = next((service.paths.archive / "raw").rglob("*.gz"))
    backup.write_bytes(gzip.compress(b"corrupt"))
    assert not service.audit_codex(codex)["ok"]


def test_two_devices_converge_and_conflicts_are_retained(tmp_path):
    codex_a, codex_b = tmp_path / "codex-a", tmp_path / "codex-b"
    session(codex_a)
    a, b = memory(tmp_path / "a"), memory(tmp_path / "b")
    remote = tmp_path / "remote"
    for service in (a, b):
        write_json(service.paths.state / "cloud.json", {"provider": "local-folder", "root": str(remote)})
    a.import_codex(codex_a)
    a.sync_now()
    b.sync_now()
    assert len(b.search("hello")) == 1
    session(codex_b, text="new message", timestamp="2026-09-18T12:00:00Z")
    b.import_codex(codex_b)
    b.sync_now()
    a.sync_now()
    assert len(a.search("new message")) == 1
    assert len(a.list_conversations()) == 1
    assert len(list((remote / "snapshots").rglob("*.json.*"))) >= 2
    assert len(list((remote / "raw").rglob("*.gz"))) == 2
    assert not list((a.paths.archive / "snapshots").rglob("*.json.*"))
    assert not list((a.paths.archive / "raw").rglob("*.gz"))
    # Re-importing an older source cannot revert the synced conversation.
    a.import_codex(codex_a, force=True)
    assert len(a.search("new message")) == 1


def test_corrupt_cloud_object_is_rejected(tmp_path):
    codex = tmp_path / "codex"
    session(codex)
    a, b = memory(tmp_path / "a"), memory(tmp_path / "b")
    remote = tmp_path / "remote"
    for service in (a, b):
        write_json(service.paths.state / "cloud.json", {"provider": "local-folder", "root": str(remote)})
    a.import_codex(codex)
    a.sync_now()
    next((remote / "snapshots").rglob("*.json.*")).write_bytes(b"bad")
    with pytest.raises(ValueError, match="checksum"):
        b.sync_now()
    assert b.status()["sync"]["status"] == "error"
    assert b.status()["conversation_count"] == 0


def test_one_invalid_source_does_not_skip_other_sessions(tmp_path):
    codex = tmp_path / "codex"
    bad = session(codex, "a")
    bad.write_text('broken\n' + bad.read_text())
    session(codex, "b")
    service = memory(tmp_path / "memory")
    result = service.import_codex(codex)
    assert result.imported == 1 and len(result.errors) == 1


def test_simultaneous_edits_with_same_timestamp_converge(tmp_path):
    a, b = memory(tmp_path / "a"), memory(tmp_path / "b")
    for service, text in ((a, "from computer A"), (b, "from computer B")):
        codex = service.paths.home / "codex"
        session(codex, text=text)
        service.import_codex(codex)
        write_json(service.paths.state / "cloud.json", {"provider": "local-folder", "root": str(tmp_path / "remote")})
    a.sync_now()
    b.sync_now()
    a.sync_now()
    key = a.list_conversations()[0]["id"]
    assert a.get_conversation(key).to_dict() == b.get_conversation(key).to_dict()
    assert len(list((tmp_path / "remote" / "snapshots").rglob("*.json.*"))) == 2


def test_legacy_inherited_session_id_cannot_replace_repaired_archive(tmp_path):
    a, b = memory(tmp_path / "a"), memory(tmp_path / "b")
    codex = tmp_path / "codex"
    session(codex)
    a.import_codex(codex)
    current = a.get_conversation(a.list_conversations()[0]["id"])
    legacy = replace(current, updated_at="2099-01-01T00:00:00Z", metadata={"parser_version": 1})
    a.accept_conversation(legacy)
    for service in (a, b):
        write_json(service.paths.state / "cloud.json", {"provider": "local-folder", "root": str(tmp_path / "remote")})
    a.sync_now()
    b.sync_now()
    assert b.get_conversation(current.id).metadata["parser_version"] == 4


def test_same_content_from_another_client_label_does_not_create_revision(tmp_path):
    service = memory(tmp_path / "memory")
    codex = tmp_path / "codex"
    session(codex)
    service.import_codex(codex)
    current = service.get_conversation(service.list_conversations()[0]["id"])
    before = list((service.paths.archive / "snapshots").rglob("*.json.*"))

    variant = replace(current, source="vscode-codex", metadata={**current.metadata, "source_path": "vscode"})

    assert service.accept_conversation(variant) is False
    assert list((service.paths.archive / "snapshots").rglob("*.json.*")) == before


def test_sync_removes_semantically_duplicate_snapshots(tmp_path):
    service = memory(tmp_path / "memory")
    codex = tmp_path / "codex"
    session(codex)
    service.import_codex(codex)
    current = service.get_conversation(service.list_conversations()[0]["id"])
    service.archive.write(
        replace(current, source="vscode-codex", metadata={**current.metadata, "source_path": "vscode"})
    )
    remote = tmp_path / "remote"
    write_json(service.paths.state / "cloud.json", {"provider": "local-folder", "root": str(remote)})

    result = service.sync_now()

    assert len(list((remote / "snapshots").rglob("*.json.*"))) == 1
    assert result["retention"]["removed_source_count"] == 1


def test_cloud_transfer_does_not_block_local_import(tmp_path, monkeypatch):
    from filelock import FileLock
    service = memory(tmp_path / "memory")
    write_json(service.paths.state / "cloud.json", {"provider": "google-drive"})
    codex = tmp_path / "codex"
    session(codex)

    class Remote:
        def list(self, progress):
            with FileLock(str(service.paths.state / "operations.lock"), timeout=0):
                pass
            assert service.import_codex(codex).imported == 1
            return set()

        def upload(self, local_path, relative):
            pass

        def download(self, relative, local_path):
            raise AssertionError("No downloads expected")

    monkeypatch.setattr("aimemory.sync.cloud_sync._remote_archive", lambda paths, config: Remote())
    service.sync_now()
    assert service.status()["conversation_count"] == 1


def test_audit_checks_captured_backup_even_when_session_has_advanced(tmp_path):
    service = memory(tmp_path / "memory")
    codex = tmp_path / "codex"
    path = session(codex)
    service.import_codex(codex)
    with path.open("a") as handle:
        handle.write(json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n")
    report = service.audit_codex(codex)
    assert report["verified_files"] == 1
    assert report["changing_files"] == [str(path)]
    assert not report["ok"]
    next((service.paths.archive / "raw").rglob("*.gz")).write_bytes(gzip.compress(b"bad"))
    report = service.audit_codex(codex)
    assert report["verified_files"] == 0 and len(report["issues"]) == 1


def test_cloud_restores_append_delta_backups(tmp_path):
    from aimemory.archive.raw_backup import read_raw
    from aimemory.state import read_json
    a, b = memory(tmp_path / "a"), memory(tmp_path / "b")
    codex = tmp_path / "codex"
    path = session(codex)
    a.import_codex(codex)
    with path.open("a") as handle:
        handle.write(json.dumps({"type": "response_item", "timestamp": "2026-09-19T12:00:00Z",
                                "payload": {"type": "message", "role": "assistant", "content": "new answer"}}) + "\n")
    a.import_codex(codex)
    entry = read_json(a.paths.state / "source-manifest.json")[str(path)]
    assert entry["raw_path"].endswith(".delta.json.gz")
    for service in (a, b):
        write_json(service.paths.state / "cloud.json", {"provider": "local-folder", "root": str(tmp_path / "remote")})
    a.sync_now()
    b.sync_now()
    assert read_raw(tmp_path / "remote", entry["raw_path"]) == path.read_bytes()
    assert b.search("new answer")


def test_synced_raw_is_pruned_without_reimport_loop(tmp_path):
    service = memory(tmp_path / "memory")
    codex = tmp_path / "codex"
    session(codex)
    remote = tmp_path / "remote"
    write_json(service.paths.state / "cloud.json", {"provider": "local-folder", "root": str(remote)})

    assert service.import_codex(codex).imported == 1
    result = service.sync_now()

    assert result["retention"]["removed_raw_count"] == 1
    assert result["retention"]["removed_revision_count"] == 1
    assert not list((service.paths.archive / "raw").rglob("*.gz"))
    assert not list((service.paths.archive / "snapshots").rglob("*.json.*"))
    assert list((remote / "raw").rglob("*.gz"))
    assert list((remote / "snapshots").rglob("*.json.*"))
    assert service.import_codex(codex).skipped == 1
    report = service.audit_codex(codex)
    assert report["ok"]
    assert report["cloud_pruned_files"] == 1


def test_changing_destination_reuploads_current_conversations(tmp_path):
    service = memory(tmp_path / "memory")
    codex = tmp_path / "codex"
    session(codex)
    first = tmp_path / "first-remote"
    second = tmp_path / "second-remote"
    write_json(
        service.paths.state / "cloud.json",
        {"provider": "local-folder", "root": str(first), "connection_id": "first"},
    )
    service.import_codex(codex)
    service.sync_now()
    assert not list((service.paths.archive / "snapshots").rglob("*.json.*"))

    write_json(
        service.paths.state / "cloud.json",
        {"provider": "local-folder", "root": str(second), "connection_id": "second"},
    )
    service.sync_now()

    assert list((second / "snapshots").rglob("*.json.*"))
    assert service.search("hello")
    # The old destination is never deleted when switching accounts.
    assert list((first / "raw").rglob("*.gz"))


def test_switch_pulls_old_cloud_conversations_before_populating_new_cloud(tmp_path):
    old_cloud = tmp_path / "old-cloud"
    new_cloud = tmp_path / "new-cloud"
    first_computer = memory(tmp_path / "first-computer")
    switching_computer = memory(tmp_path / "switching-computer")

    old_source = tmp_path / "old-source"
    session(old_source, "old", text="conversation from old cloud")
    first_computer.import_codex(old_source)
    write_json(
        first_computer.paths.state / "cloud.json",
        {"provider": "local-folder", "root": str(old_cloud), "connection_id": "old"},
    )
    first_computer.sync_now()

    local_source = tmp_path / "local-source"
    session(local_source, "local", text="conversation from this computer")
    switching_computer.import_codex(local_source)
    write_json(
        switching_computer.paths.state / "cloud.json",
        {"provider": "local-folder", "root": str(old_cloud), "connection_id": "old"},
    )
    old_object_count = len(list((old_cloud / "snapshots").rglob("*.json.*")))

    switching_computer.pull_cloud_now()

    assert switching_computer.search("conversation from old cloud")
    assert len(list((old_cloud / "snapshots").rglob("*.json.*"))) == old_object_count

    write_json(
        switching_computer.paths.state / "cloud.json",
        {"provider": "local-folder", "root": str(new_cloud), "connection_id": "new"},
    )
    switching_computer.sync_now()

    restored = memory(tmp_path / "restored")
    write_json(
        restored.paths.state / "cloud.json",
        {"provider": "local-folder", "root": str(new_cloud), "connection_id": "new"},
    )
    restored.sync_now()
    assert restored.search("conversation from old cloud")
    assert restored.search("conversation from this computer")


def test_local_cloud_provider_reports_insufficient_space(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from aimemory.sync.cloud_sync import _ensure_available_space
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "big.bin").write_bytes(b"x" * 1024)
    monkeypatch.setattr(
        "aimemory.sync.cloud_sync.shutil.disk_usage",
        lambda path: SimpleNamespace(free=16),
    )

    with pytest.raises(RuntimeError, match="Espace insuffisant"):
        _ensure_available_space(source, destination)
