import hashlib
import json
from pathlib import Path

import pytest

from aimemory.adapters.codex import CodexAdapter
from aimemory.adapters.claude import ClaudeAdapter
from aimemory.adapters.vscode import VSCodeAdapter
from aimemory.archive.raw_backup import read_raw, write_raw
from aimemory.service import MemoryService
from aimemory.source_snapshot import SourceSnapshot, ParseCache
from aimemory.state import read_json


def encoded(*records):
    return b"".join(json.dumps(row, ensure_ascii=False).encode() + b"\n" for row in records)


@pytest.mark.parametrize("kind", ["codex", "claude"])
def test_append_matches_full_parse_and_only_decodes_tail(tmp_path, monkeypatch, kind):
    monkeypatch.setenv("AI_MEMORY_HOME", str(tmp_path / "memory"))
    home = tmp_path / kind
    path = home / ("sessions/a.jsonl" if kind == "codex" else "projects/-tmp-project/a.jsonl")
    path.parent.mkdir(parents=True)
    if kind == "codex":
        initial = encoded(
            {"type": "session_meta", "payload": {"id": "example", "cwd": "/tmp/project"}},
            {"timestamp": "2026-10-28T10:00:00Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": "bonjour"}},
            {"type": "response_item", "payload": {"type": "function_call", "call_id": "c", "name": "shell", "arguments": "{}"}},
        )
        tail = encoded({"timestamp": "2026-10-28T10:01:00Z", "type": "response_item", "payload": {"type": "function_call_output", "call_id": "c", "output": "terminé"}})
        adapter = lambda: CodexAdapter(home)
    else:
        initial = encoded(
            {"sessionId": "example", "cwd": "/tmp/project", "type": "user", "timestamp": "2026-10-28T10:00:00Z", "message": {"role": "user", "content": "bonjour"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "c", "name": "Bash", "input": {"command": "pwd"}}]}},
        )
        tail = encoded({"type": "user", "timestamp": "2026-10-28T10:01:00Z", "message": {"content": [{"type": "tool_result", "tool_use_id": "c", "content": "terminé"}]}})
        adapter = lambda: ClaudeAdapter(home)
    path.write_bytes(initial)
    service = MemoryService()
    assert service._import_adapter(adapter(), force=False).imported == 1
    path.write_bytes(initial + tail)
    import aimemory.adapters.common as common
    original = common.iter_jsonl
    decoded = []
    def count(*args, **kwargs):
        for record in original(*args, **kwargs):
            decoded.append(record)
            yield record
    monkeypatch.setattr("aimemory.adapters." + kind + ".iter_jsonl", count)
    result = service._import_adapter(adapter(), force=False)
    assert result.errors == []
    assert result.imported == 1
    assert len(decoded) == 1
    entry = read_json(service.paths.state / "source-manifest.json")[str(path)]
    actual = service.get_conversation(entry["conversation_id"])
    expected = adapter().parse_session(path)
    for key in ("messages", "tool_calls", "created_at", "updated_at", "commands", "source_session_id"):
        assert actual.to_dict()[key] == expected.to_dict()[key]
    assert actual.tool_calls[0].output == "terminé"
    assert read_raw(service.paths.archive, entry["raw_path"]) == initial + tail


@pytest.mark.parametrize("edit", ["rewrite", "truncate", "partial", "partial_newline", "replace"])
def test_incremental_fallback_preserves_edits(tmp_path, monkeypatch, edit):
    monkeypatch.setenv("AI_MEMORY_HOME", str(tmp_path / "memory"))
    home = tmp_path / "codex"
    path = home / "sessions/a.jsonl"
    path.parent.mkdir(parents=True)
    record = lambda text: {"timestamp": "2026-10-28T10:00:00Z" if text == "one" else "2026-10-28T10:01:00Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": text}}
    initial = encoded({"type": "session_meta", "payload": {"id": "a"}}, record("one"))
    if edit.startswith("partial"):
        initial += b'{"type":' + (b"\n" if edit == "partial_newline" else b"")
    path.write_bytes(initial)
    service = MemoryService()
    assert not service.import_codex(home).errors
    if edit == "truncate":
        final = encoded(record("new"))
    elif edit.startswith("partial"):
        final = initial + b'"response_item", "payload": {"type":"message", "role":"user", "content":"two"}}\n'
        if edit == "partial_newline":
            # Previously incomplete final line is now corruption in the middle.
            path.write_bytes(final)
            assert service.import_codex(home).errors
            return
    else:
        final = initial.replace(b'one', b'new') + encoded(record("two"))
    if edit == "replace":
        replacement = path.with_suffix(".tmp")
        replacement.write_bytes(final)
        replacement.replace(path)
    else:
        path.write_bytes(final)
    result = service.import_codex(home)
    assert not result.errors
    entry = read_json(service.paths.state / "source-manifest.json")[str(path)]
    assert read_raw(service.paths.archive, entry["raw_path"]) == final
    archived = service.archive.read(service.archive.path_for(CodexAdapter(home).parse_session(path)))
    assert archived.to_dict()["messages"] == CodexAdapter(home).parse_session(path).to_dict()["messages"]


def test_large_source_streams_and_deltas_stay_portable(tmp_path, monkeypatch):
    path = tmp_path / "source.jsonl"
    initial = b"x" * (3 * 1024 * 1024 + 2)
    path.write_bytes(initial)
    # SourceSnapshot and streamed backup must not call read_bytes on the source.
    original = Path.read_bytes
    def read(candidate):
        assert candidate != path
        return original(candidate)
    monkeypatch.setattr(Path, "read_bytes", read)
    with SourceSnapshot(path, tmp_path) as snapshot:
        assert snapshot.stream._rolled
        first = write_raw(tmp_path, "a", snapshot, snapshot.digest, {})
    tail = b"extra" * 300001
    path.write_bytes(initial + tail)
    previous = {"size": len(initial), "sha256": hashlib.sha256(initial).hexdigest(), "raw_path": str(first)}
    with SourceSnapshot(path, tmp_path, (len(initial),)) as snapshot:
        second = write_raw(tmp_path, "a", snapshot, snapshot.digest, previous)
    assert str(second).endswith(".delta.json.gz")
    assert read_raw(tmp_path, str(second)) == initial + tail


def test_cache_budget_evicts_and_partial_records_are_not_cached(tmp_path):
    path = tmp_path / "source.jsonl"
    path.write_bytes(b"{}\n")
    cache = ParseCache(budget=1024)
    with SourceSnapshot(path, tmp_path) as snapshot:
        cache.put("first", snapshot, {"count": 1})
        assert cache.pop("first") is not None
        cache.put("large", snapshot, {"text": "x" * 2048})
        cache.put("partial", snapshot, {"partial": True})
        assert not cache.entries


def test_vscode_mutation_append_matches_full_parse(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MEMORY_HOME", str(tmp_path / "memory"))
    home = tmp_path / "code"
    source = home / "workspaceStorage/project/chatSessions/a.jsonl"
    source.parent.mkdir(parents=True)
    initial = encoded({"kind": 0, "v": {"sessionId": "a", "requests": [{"message": "first"}]}})
    source.write_bytes(initial)
    service = MemoryService()
    assert service._import_adapter(VSCodeAdapter(home), False).imported == 1
    source.write_bytes(initial + encoded({"kind": 2, "k": ["requests"], "v": [{"message": "second"}]}))
    assert service._import_adapter(VSCodeAdapter(home), False).imported == 1
    entry = read_json(service.paths.state / "source-manifest.json")[str(source)]
    actual = service.get_conversation(entry["conversation_id"])
    assert actual.to_dict()["messages"] == VSCodeAdapter(home).parse_session(source).to_dict()["messages"]
    assert read_raw(service.paths.archive, entry["raw_path"]) == source.read_bytes()


def test_source_changed_during_capture_is_retried(tmp_path, monkeypatch):
    from aimemory.source_snapshot import SourceChanged
    source = tmp_path / "a.jsonl"
    source.write_bytes(encoded({"message": "first"}))
    original = Path.stat
    def modified(path, *args, **kwargs):
        if path == source:
            source.write_bytes(encoded({"message": "second"}))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", modified)
    with pytest.raises(SourceChanged):
        SourceSnapshot(source, tmp_path)


def test_cache_is_lru_and_has_four_session_limit(tmp_path):
    source = tmp_path / "a.jsonl"
    source.write_bytes(b"{}\n")
    cache = ParseCache()
    with SourceSnapshot(source, tmp_path) as snapshot:
        for index in range(5):
            cache.put(str(index), snapshot, {"count": index})
        assert cache.pop("0") is None
        assert len(cache.entries) == 4
