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
    assert len(list((a.paths.archive / "snapshots").rglob("*.json.*"))) >= 2
    assert len(list((a.paths.archive / "raw").rglob("*.gz"))) == 2
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
    assert len(list((a.paths.archive / "snapshots").rglob("*.json.*"))) == 2


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
    assert b.get_conversation(current.id).metadata["parser_version"] == 2
