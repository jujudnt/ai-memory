import hashlib
import json
from dataclasses import replace

import pytest

from aimemory.adapters.claude import ClaudeAdapter
from aimemory.archive.raw_backup import read_raw, validate_raw, write_raw
from aimemory.cloud.errors import CloudError, error_category
from aimemory.desktop import _pull_current_destination
from aimemory.mcp.tools import MemoryToolHandlers
from aimemory.state import read_json, write_json
from aimemory.sync.cloud_sync import LocalArchiveRemote
from test_cloud_integrity import memory, session


def connect(service, root, identity):
    write_json(service.paths.state / "cloud.json", {
        "provider": "local-folder", "root": str(root), "connection_id": identity,
    })


def inventory(root):
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_migration_preserves_every_pruned_revision_and_original(tmp_path):
    service = memory(tmp_path / "memory")
    source = tmp_path / "source"
    connect(service, tmp_path / "old", "old")
    for text, date in [("older", "2026-09-18"), ("newer", "2026-09-19")]:
        session(source, text=text, timestamp=date + "T10:00:00Z")
        service.import_codex(source)
        service.sync_now()
    assert not inventory(service.paths.archive / "raw")
    _pull_current_destination(service)
    expected = inventory(tmp_path / "old")
    assert len(expected) == 4
    assert read_json(service.paths.state / "migration.json")["status"] == "prepared"
    connect(service, tmp_path / "new", "new")
    service.sync_now()
    assert inventory(tmp_path / "new") == expected
    assert inventory(tmp_path / "old") == expected
    assert not (service.paths.state / "migration.json").exists()


def test_same_destination_pull_does_not_block_local_retention(tmp_path):
    service = memory(tmp_path / "memory")
    codex = tmp_path / "codex"
    session(codex)
    connect(service, tmp_path / "remote", "same-destination")
    service.import_codex(codex)
    service.sync_now()

    # A reconnect or a cancelled destination change may prepare a migration
    # against the destination that is already active.
    pulled = service.pull_cloud_now()
    assert pulled["status"] == "synced"
    migration = read_json(service.paths.state / "migration.json")
    assert migration["status"] == "prepared"
    assert migration["source_identity"] == "same-destination"
    local_snapshot = next((tmp_path / "remote" / "snapshots").rglob("*.json.*"))
    archived_snapshot = service.paths.archive / local_snapshot.relative_to(tmp_path / "remote")
    assert archived_snapshot.is_file()

    result = service.sync_now()

    assert result["status"] == "synced"
    assert not (service.paths.state / "migration.json").exists()
    assert not archived_snapshot.exists()
    assert local_snapshot.is_file()


def test_same_destination_migration_keeps_local_copy_if_remote_is_incomplete(tmp_path):
    service = memory(tmp_path / "memory")
    codex = tmp_path / "codex"
    session(codex)
    connect(service, tmp_path / "remote", "same-destination")
    service.import_codex(codex)
    write_json(service.paths.state / "migration.json", {
        "status": "preparing", "source_identity": "same-destination",
        "objects": ["snapshots/missing/" + "0" * 64 + ".json.zst"],
    })

    result = service.sync_now()

    assert result["status"] == "synced"
    assert (service.paths.state / "migration.json").exists()
    assert result["retention"] == {}
    assert list((service.paths.archive / "raw").rglob("*.gz"))


def test_download_resume_does_not_receive_validated_objects_twice(tmp_path, monkeypatch):
    a, b = memory(tmp_path / "a"), memory(tmp_path / "b")
    source, root = tmp_path / "source", tmp_path / "remote"
    session(source)
    a.import_codex(source)
    for service in (a, b):
        connect(service, root, "remote")
    a.sync_now()
    remote = LocalArchiveRemote(root)
    original = remote.download
    calls = []
    def download(relative, path):
        calls.append(relative)
        if len(calls) == 2:
            raise CloudError("network disconnected")
        original(relative, path)
    remote.download = download
    monkeypatch.setattr("aimemory.sync.cloud_sync._remote_archive", lambda *args: remote)
    with pytest.raises(CloudError):
        b.sync_now()
    assert calls[0] in read_json(b.paths.state / "synced-objects.json")
    b.sync_now()
    assert calls.count(calls[0]) == 1
    assert b.search("hello")


@pytest.mark.parametrize("category,action", [("transient", False), ("quota", False), ("auth", True)])
def test_cloud_failure_classification(tmp_path, monkeypatch, category, action):
    service = memory(tmp_path / "memory")
    write_json(service.paths.state / "cloud.json", {"provider": "icloud-online"})
    class Remote:
        def list(self, progress):
            raise CloudError("synthetic failure", category)
    monkeypatch.setattr("aimemory.sync.cloud_sync._remote_archive", lambda *args: Remote())
    with pytest.raises(CloudError):
        service.sync_now()
    status = read_json(service.paths.state / "sync-status.json")
    assert status["requires_action"] is action
    assert status["error_category"] == category
    assert status["retry_after"] > 0


def test_cloud_full_is_not_an_authentication_error():
    assert error_category("403 storageQuotaExceeded") == "quota"
    assert error_category("invalid_grant") == "auth"
    assert error_category("dial tcp: no such host") == "transient"


def test_append_after_cloud_pruning_is_incremental(tmp_path):
    service = memory(tmp_path / "memory")
    source = tmp_path / "source"
    path = session(source)
    service.import_codex(source)
    connect(service, tmp_path / "remote", "remote")
    service.sync_now()
    with path.open("a") as stream:
        stream.write(json.dumps({"type": "response_item", "timestamp": "2026-09-21T00:00:00Z",
                                "payload": {"type": "message", "role": "assistant", "content": "tail"}}) + "\n")
    service.import_codex(source)
    entry = read_json(service.paths.state / "source-manifest.json")[str(path)]
    assert entry["raw_path"].endswith(".delta.json.gz")
    service.sync_now()
    assert read_raw(tmp_path / "remote", entry["raw_path"]) == path.read_bytes()


def test_bounded_raw_chain_and_streaming_verification(tmp_path):
    raw, previous, verified = b"first", {}, {}
    for index in range(36):
        raw += f"line {index}\n".encode()
        path = write_raw(tmp_path, "session", raw, hashlib.sha256(raw).hexdigest(), previous)
        depth = previous.get("raw_depth", 0) + 1 if path.name.endswith(".delta.json.gz") else 0
        previous = {"raw_path": str(path), "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "raw_depth": depth}
        validate_raw(tmp_path, str(path), verified)
        assert depth <= 32
    assert read_raw(tmp_path, str(path)) == raw


def test_claude_subagents_never_replace_main_and_tool_results_link(tmp_path):
    adapter = ClaudeAdapter(tmp_path / "claude")
    raw = (json.dumps({"sessionId": "parent", "type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {"command": "pwd"}}]}}) + "\n" +
        json.dumps({"sessionId": "parent", "type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tool-1", "content": "/project"}]}})).encode()
    main = adapter.parse_session(tmp_path / "claude/projects/test/parent.jsonl", raw)
    child = adapter.parse_session(tmp_path / "claude/projects/test/parent/subagents/agent-x.jsonl", raw)
    assert main.id != child.id
    assert child.metadata["parent_session_id"] == "parent"
    assert main.tool_calls[0].output == "/project"


def test_mcp_filters_dates_sources_projects_and_pagination(tmp_path):
    service = memory(tmp_path / "memory")
    session(tmp_path / "source", text="search phrase")
    service.import_codex(tmp_path / "source")
    conversation = service.get_conversation(service.list_conversations()[0]["id"])
    from aimemory.models import ProjectIdentity
    conversation = replace(conversation, source="vscode-codex", project=ProjectIdentity("p1", "Client"))
    service.db.upsert_conversation(conversation, service.archive.write(conversation))
    tools = MemoryToolHandlers(service)
    assert not tools.search_conversations("phrase", date_from="2099-01-01")
    assert tools.search_conversations("phrase", source="codex", project="client", date_to="2026-09-18")
    assert not tools.list_conversations(offset=1)
    assert tools.list_projects()[0]["name"] == "Client"


def test_icloud_local_does_not_claim_confirmed_server_upload(tmp_path):
    service = memory(tmp_path / "memory")
    session(tmp_path / "source")
    service.import_codex(tmp_path / "source")
    write_json(service.paths.state / "cloud.json", {"provider": "icloud-drive", "root": str(tmp_path / "remote")})
    status = service.sync_now()
    assert status["confirmation"] == "local_folder"
    assert inventory(service.paths.archive / "raw")


def test_database_connections_close_on_exceptions(tmp_path):
    service = memory(tmp_path / "memory")
    import sqlite3
    with pytest.raises(RuntimeError), service.db.connect() as connection:
        raise RuntimeError("injected")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_vscode_journal_replays_appends_replacements_and_deletes(tmp_path):
    from aimemory.adapters.vscode import VSCodeAdapter
    records = [
        {"kind": 0, "v": {"sessionId": "session", "requests": []}},
        {"kind": 2, "k": ["requests"], "v": [{"message": "question", "response": "draft"}]},
        {"kind": 1, "k": ["requests", 0, "response"], "v": "final answer"},
        {"kind": 2, "k": ["requests"], "v": [{"message": "discarded"}]},
        {"kind": 2, "k": ["requests"], "i": 1},
        {"kind": 1, "k": ["customTitle"], "v": "temporary"},
        {"kind": 3, "k": ["customTitle"]},
    ]
    conversation = VSCodeAdapter(tmp_path).parse_session(tmp_path / "session.jsonl",
        ("\n".join(json.dumps(item) for item in records)).encode())
    assert [m.content for m in conversation.messages] == ["question", "final answer"]
    assert conversation.title == "question"


def test_migration_waiting_for_local_icloud_cannot_change_destination(tmp_path, monkeypatch):
    service = memory(tmp_path / "memory")
    connect(service, tmp_path / "old", "old")
    monkeypatch.setattr(service, "pull_cloud_now", lambda: {"status": "waiting_local_cloud"})
    with pytest.raises(ValueError, match="suspendu"):
        _pull_current_destination(service)
    assert read_json(service.paths.state / "cloud.json")["connection_id"] == "old"


def test_onedrive_selects_drive_without_restarting_oauth(tmp_path, monkeypatch):
    from aimemory.cloud.rclone_provider import RcloneCloudProvider
    provider = RcloneCloudProvider.for_provider(memory(tmp_path / "memory").paths, "onedrive-online")
    calls = []
    def run(args, timeout=600, config=None):
        calls.append(args)
        if args[:2] == ["config", "create"]:
            config.write_text('[aimemory-onedrive]\ntype = onedrive\ntoken = {"access_token":"test"}\n')
            return json.dumps({"State": "choose-drive", "Option": {"Name": "config_driveid", "Examples": [
                {"Value": "drive-A", "Help": "Personal"}, {"Value": "drive-B", "Help": "Work"}]}})
        if args[:2] == ["config", "update"] and "choose-drive" in args:
            assert args[args.index("--result") + 1] == "drive-B"
            return json.dumps({"State": "confirm-drive", "Option": {"Name": "config_driveok"}})
        return "{}"
    monkeypatch.setattr(provider, "run", run)
    assert provider.connect()["status"] == "needs_selection"
    assert not (provider.paths.state / "cloud.json").exists()
    provider.connect({"selection": "drive-B"})
    assert sum(args[:2] == ["config", "create"] for args in calls) == 1
    assert read_json(provider.paths.state / "cloud.json")["provider"] == "onedrive-online"


def test_installer_handles_claude_only_without_creating_codex_config(tmp_path, monkeypatch):
    import aimemory.installer as installer
    monkeypatch.setattr(installer, "_install_cli_runtime", lambda: False)
    monkeypatch.setattr(installer, "codex_available", lambda: False)
    monkeypatch.setattr(installer, "claude_desktop_available", lambda: False)
    monkeypatch.setattr(installer, "claude_code_available", lambda: True)
    monkeypatch.setattr(installer, "claude_code_config_path", lambda: tmp_path / ".claude.json")
    monkeypatch.setattr(installer, "vscode_user_mcp_config_path", lambda: None)
    monkeypatch.setattr(installer, "install_mcp_config", lambda *args, **kw: pytest.fail("Codex absent"))
    result = installer.install_all_mcp_configs()
    assert "Claude Code" in result.message
    assert "ai-memory" in json.loads((tmp_path / ".claude.json").read_text())["mcpServers"]


def test_parser_upgrade_cannot_replace_a_more_recent_cloud_conversation(tmp_path):
    service = memory(tmp_path / "memory")
    session(tmp_path / "source")
    service.import_codex(tmp_path / "source")
    current = service.get_conversation(service.list_conversations()[0]["id"])
    recent = replace(current, updated_at="2026-09-22T10:00:00Z", metadata={"parser_version": 4})
    service.accept_conversation(recent)
    assert not service.accept_conversation(current)
    assert service.get_conversation(current.id).updated_at == recent.updated_at


def test_icloud_temporary_failure_after_code_keeps_trusted_session(tmp_path, monkeypatch):
    from aimemory.cloud.rclone_provider import RcloneCloudProvider
    provider = RcloneCloudProvider.for_provider(memory(tmp_path / "memory").paths, "icloud-online")
    provider.icloud_pending_config.parent.mkdir(parents=True)
    provider.icloud_pending_config.write_text("[aimemory-icloud]\ntype=iclouddrive\ntrust_token=test\n")
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise CloudError("temporary network error")
        return ""
    monkeypatch.setattr(provider, "run", run)
    assert provider.connect({"resume_after_approval": True})["status"] == "needs_access_retry"
    assert provider.icloud_pending_config.exists()
    assert provider.connect({"resume_after_approval": True})["status"] == "connected"
    assert all(args[0] == "mkdir" for args in calls)


def test_compaction_recovers_free_pages_without_touching_archives(tmp_path, monkeypatch):
    from aimemory.cleanup import compact_search_index
    service = memory(tmp_path / "memory")
    service.db.initialize()
    with service.db.connect() as conn:
        conn.execute("CREATE TABLE disposable(data BLOB)")
        conn.execute("INSERT INTO disposable VALUES (zeroblob(262144))")
        conn.execute("DELETE FROM disposable")
    monkeypatch.setattr("aimemory.cleanup.COMPACT_MIN_BYTES", 1)
    result = compact_search_index(service.paths)
    assert result["freed_bytes"] > 0
    with service.db.connect() as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_compaction_defers_during_cloud_transfer(tmp_path):
    from aimemory.cleanup import compact_search_index
    from filelock import FileLock
    service = memory(tmp_path / "memory")
    service.db.initialize()
    with FileLock(str(service.paths.state / "sync.lock")):
        assert compact_search_index(service.paths)["status"] == "busy"


def test_corrupt_cached_raw_parent_is_retried_not_stuck(tmp_path, monkeypatch):
    import gzip
    service = memory(tmp_path / "memory")
    raw = b"original"
    parent = write_raw(tmp_path / "remote", "thread", raw, hashlib.sha256(raw).hexdigest(), {})
    complete = raw + b" append"
    child = write_raw(tmp_path / "remote", "thread", complete, hashlib.sha256(complete).hexdigest(),
                      {"raw_path": str(parent), "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    connect(service, tmp_path / "remote", "remote")
    local_parent = service.paths.archive / parent
    local_parent.parent.mkdir(parents=True)
    local_parent.write_bytes(gzip.compress(b"corrupt"))
    with pytest.raises(ValueError, match="checksum"):
        service.sync_now()
    assert not local_parent.exists()
    assert service.sync_now()["status"] == "synced"
    assert read_raw(tmp_path / "remote", str(child)) == complete


def test_broken_packaged_runtime_is_refused(tmp_path, monkeypatch):
    from aimemory.installer import _validate_cli_runtime
    from types import SimpleNamespace
    monkeypatch.setattr("aimemory.installer.subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=255))
    with pytest.raises(RuntimeError, match="conservée"):
        _validate_cli_runtime(tmp_path / "ai-memory-cli")
