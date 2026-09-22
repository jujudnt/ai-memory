import hashlib
import sqlite3
from pathlib import Path

import pytest

from aimemory.adapters.codex import CodexAdapter
from aimemory.cleanup import compact_search_index
from aimemory.db.codec import pack_text, unpack_text
from aimemory.models import ToolCall
from aimemory.state import now, write_json
from test_cloud_integrity import memory, session


@pytest.mark.parametrize("text", [None, "", "short", "é漢字\x00" * 10000],
                         ids=["null", "empty", "short", "long-unicode"])
def test_lossless_codec(text):
    assert unpack_text(pack_text(text)) == text


def test_compact_migration_preserves_archives_and_full_tool_data(tmp_path):
    service = memory(tmp_path / "memory")
    conversation = CodexAdapter(tmp_path).parse_session(session(tmp_path / "source"))
    output = "toolonlyneedle " * 10000
    arguments = '{"cmd":"pytest"}' * 10000
    conversation.tool_calls = [ToolCall("tool", "exec", arguments=arguments, output=output)]
    path = service.archive.write(conversation)
    service.db.upsert_conversation(conversation, path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    # Simulate an older database with uncompressed fields and the verbose FTS entry.
    with service.db.connect() as conn:
        conn.execute("DELETE FROM compact_index_versions")
        conn.execute("UPDATE tool_calls SET arguments=?, output=?", (arguments, output))
        conn.execute("UPDATE conversations_fts SET content=?", (output,))
    assert service.db.search("toolonlyneedle")
    assert service._repair_compact_index() == 1
    assert service._repair_compact_index() == 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert not service.db.search("toolonlyneedle")
    assert service.db.search("pytest")
    with service.db.connect() as conn:
        row = conn.execute("SELECT id, arguments, output FROM tool_calls").fetchone()
        assert isinstance(row["output"], bytes)
        assert len(row["output"]) < len(output) / 10
        assert unpack_text(row["output"]) == output
        assert unpack_text(row["arguments"]) == arguments
    page = service.db.conversation_page(conversation.id, include_tools=True, limit=1)
    assert page["tool_calls"][0]["output"] == output[:2000]
    recovered, offset = [], 0
    while True:
        excerpt = service.db.message_excerpt(conversation.id, row["id"], offset=offset, field="output")
        recovered.append(excerpt["text"])
        offset = excerpt["next_offset"]
        if offset is None:
            break
    assert "".join(recovered) == output
    # Legacy TEXT still reads correctly, even if an old client writes to the DB.
    with service.db.connect() as conn:
        conn.execute("UPDATE tool_calls SET output='legacy'")
    assert service.db.message_excerpt(conversation.id, row["id"], field="output")["text"] == "legacy"
    compact_search_index(service.paths, force=True)
    with sqlite3.connect(service.db.path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_failed_migration_is_retryable_without_marking_done(tmp_path, monkeypatch):
    service = memory(tmp_path / "memory")
    conversation = CodexAdapter(tmp_path).parse_session(session(tmp_path / "source"))
    service.accept_conversation(conversation)
    with service.db.connect() as conn:
        conn.execute("DELETE FROM compact_index_versions")
    def fail(*args, **kwargs):
        raise OSError("temporarily unreadable")
    monkeypatch.setattr(service.archive, "read", fail)
    with pytest.raises(OSError):
        service._repair_compact_index()
    assert len(service.db.pending_compact_entries()) == 1


def test_maintenance_waits_for_migration_and_respects_sync_lock(tmp_path):
    from filelock import FileLock
    service = memory(tmp_path / "memory")
    conversation = CodexAdapter(tmp_path).parse_session(session(tmp_path / "source"))
    service.accept_conversation(conversation)
    write_json(service.paths.state / "index-maintenance.json", {"last_run_at": now()})
    with service.db.connect() as conn:
        conn.execute("DELETE FROM compact_index_versions")
    assert compact_search_index(service.paths)["status"] == "migrating"
    service._repair_compact_index()
    with FileLock(str(service.paths.state / "sync.lock")):
        assert compact_search_index(service.paths)["status"] == "busy"
    assert compact_search_index(service.paths)["compact_index_version"] == 1


def test_fts_rowid_migration_preserves_search_and_prevents_duplicate_rows(tmp_path):
    from aimemory.db.sqlite import MemoryDatabase
    service = memory(tmp_path / "memory")
    conversation = CodexAdapter(tmp_path).parse_session(session(tmp_path / "source"))
    service.accept_conversation(conversation)
    with service.db.connect() as conn:
        conn.execute("DROP TABLE fts_conversation_rows")
        conn.execute("DROP TABLE index_migrations")
    database = MemoryDatabase(service.db.path)
    database.initialize()
    assert database.search("hello")
    for _ in range(3):
        database.upsert_conversation(conversation, Path(service.db.archive_entries()[0]["archive_path"]))
    with database.connect() as conn:
        assert conn.execute("SELECT count(*) FROM conversations_fts").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM fts_conversation_rows").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM fts_conversation_rows m JOIN conversations_fts f ON f.rowid=m.fts_rowid AND f.conversation_id=m.conversation_id").fetchone()[0] == 1
    assert database.search("hello")


@pytest.mark.parametrize("preferences,status", [({"paused": True}, {}), ({}, {"retry_after": 99999999999})])
def test_watcher_maintains_local_index_while_cloud_is_paused(tmp_path, monkeypatch, preferences, status):
    from types import SimpleNamespace
    from aimemory.watcher.service import WatcherService
    service = memory(tmp_path / "memory")
    write_json(service.paths.state / "sync-preferences.json", preferences)
    write_json(service.paths.state / "sync-status.json", status)
    monkeypatch.setattr(service, "import_all", lambda **_: SimpleNamespace(scanned=0, imported=0, skipped=0, errors=[]))
    calls = []
    monkeypatch.setattr("aimemory.watcher.service.compact_search_index", lambda paths: calls.append(paths))
    monkeypatch.setattr(service, "sync_now", lambda: pytest.fail("Cloud should remain paused"))
    WatcherService(service).scan_once()
    assert calls == [service.paths]
