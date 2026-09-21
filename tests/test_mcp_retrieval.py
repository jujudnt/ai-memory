import json
from dataclasses import replace

import pytest

from aimemory.adapters.codex import CodexAdapter
from aimemory.adapters.codex_messages import deduplicate_messages
from aimemory.mcp.tools import MemoryToolHandlers
from aimemory.models import DeviceIdentity, Message, ProjectIdentity, ToolCall
from test_cloud_integrity import memory, session


def message(kind, ordinal, second=0, text="continue", role="user"):
    return Message(str(ordinal), role, text, f"2026-09-21T12:00:{second:02d}.000Z",
                   ordinal, {"source_type": kind})


def test_mirrors_are_removed_but_real_repetitions_survive():
    messages = [message("UserMessage", 0), message("message", 1),
                message("UserMessage", 2), message("message", 3),
                message("message", 4), message("UserMessage", 5, second=30)]
    result = deduplicate_messages(messages)
    assert [m.id for m in result] == ["1", "3", "4", "5"]
    assert deduplicate_messages(result) == result
    reversed_pair = [message("message", 0, role="assistant"),
                     message("AgentMessage", 1, role="assistant")]
    assert [m.id for m in deduplicate_messages(reversed_pair)] == ["0"]
    orphan = [message("UserMessage", 0), message("message", 1), message("UserMessage", 2)]
    normalized = deduplicate_messages(orphan)
    assert len(normalized) == 2
    assert deduplicate_messages(normalized) == normalized
    assert len(deduplicate_messages([message("UserMessage", 0),
                                    message("message", 1, text="changed")])) == 2


@pytest.mark.parametrize("role,event", [("user", "UserMessage"), ("assistant", "AgentMessage")])
def test_adapter_collapses_event_and_response_records(tmp_path, role, event):
    raw = "\n".join(json.dumps(record) for record in [
        {"type": "session_meta", "payload": {"id": "same-session"}},
        {"timestamp": "2026-09-21T00:00:00.001Z", "type": "event_msg",
         "payload": {"type": event, "content": "same message"}},
        {"timestamp": "2026-09-21T00:00:00.009Z", "type": "response_item",
         "payload": {"type": "message", "role": role, "content": [{"type": "text", "text": "same message"}]}},
    ]).encode()
    conversation = CodexAdapter(tmp_path).parse_session(tmp_path / "session.jsonl", raw)
    assert [(m.role, m.content) for m in conversation.messages] == [(role, "same message")]


def test_old_remote_archive_migrates_once_and_preserves_identity(tmp_path):
    service = memory(tmp_path / "memory")
    source = session(tmp_path / "source")
    old = CodexAdapter(tmp_path / "source").parse_session(source)
    old.messages = [message("UserMessage", 0), message("message", 1)]
    old.metadata.pop("message_normalization_version")
    path = service.archive.write(old)
    original = path.read_bytes()
    service.db.upsert_conversation(old, path)
    service.import_codex(tmp_path / "no-local-source")
    page = MemoryToolHandlers(service).get_conversation(old.id)
    assert page["message_count"] == 1
    assert page["source_session_id"] == old.source_session_id
    assert service.archive.read(path, normalize=False).messages[0].id == "1"
    revisions = list((service.paths.archive / "snapshots" / old.id).iterdir())
    assert any(p.read_bytes() == original for p in revisions)
    service.import_codex(tmp_path / "no-local-source")
    assert set(revisions) == set((service.paths.archive / "snapshots" / old.id).iterdir())
    assert service.accept_conversation(old) is False
    assert MemoryToolHandlers(service).get_conversation(old.id)["message_count"] == 1


def test_indexed_pages_never_open_archive_and_bound_large_payloads(tmp_path, monkeypatch):
    service = memory(tmp_path / "memory")
    source = session(tmp_path / "source")
    conversation = CodexAdapter(tmp_path / "source").parse_session(source)
    conversation.messages = [message("message", i, text=f"message-{i}-" + "x" * 10000)
                             for i in range(45)]
    conversation.messages.append(message("message", 45, text="system context", role="developer"))
    conversation.tool_calls = [ToolCall("t1", "exec", arguments="a" * 100000, output="o" * 100000)]
    service.accept_conversation(conversation)
    monkeypatch.setattr(service.archive, "read", lambda *_: pytest.fail("MCP must use the index"))
    tools = MemoryToolHandlers(service)
    page = tools.get_conversation(conversation.id, latest=True, limit=6)
    assert page["message_count"] == 45
    assert [m["ordinal"] for m in page["messages"]] == list(range(39, 45))
    assert page["tool_calls"] == []
    assert page["next_offset"] == 6
    older = tools.get_conversation(conversation.id, latest=True, offset=6, limit=6)
    assert [m["ordinal"] for m in older["messages"]] == list(range(33, 39))
    last = tools.get_conversation(conversation.id, latest=True, offset=42, limit=6)
    assert [m["ordinal"] for m in last["messages"]] == [0, 1, 2]
    assert last["next_offset"] is None
    assert tools.get_conversation(conversation.id, offset=100, latest=True)["messages"] == []
    entry = page["messages"][-1]
    assert entry["truncated"]
    rest = tools.get_message(conversation.id, entry["id"], offset=2000)
    assert rest["text"] == conversation.messages[44].content[2000:10000]
    assert tools.get_message("wrong-conversation", entry["id"]) is None
    assert len(json.dumps(tools.get_conversation(conversation.id, include_tools=True,
                                                include_context=True, limit=200, max_chars=100000))) < 60000


def test_other_mac_project_search_and_spelling_candidates(tmp_path, monkeypatch):
    service = memory(tmp_path / "memory")
    source = session(tmp_path / "source", text="voice experiments")
    base = CodexAdapter(tmp_path / "source").parse_session(source)
    project = ProjectIdentity("project-remote", "Sabai", "/remote/sabai")
    remote = replace(base, id="remote", device=DeviceIdentity("remote-device", "Work Mac"),
                     project=project, source_session_id="remote-session")
    local = replace(base, id="local", device=DeviceIdentity("local-device", "Personal Mac"),
                    project=project, source_session_id="local-session")
    service.accept_conversation(remote)
    service.accept_conversation(local)
    monkeypatch.setattr("aimemory.mcp.tools.stable_device_id", lambda: "local-device")
    tools = MemoryToolHandlers(service)
    found = tools.search_conversations("Trouve-moi la conversation Sabai sur mon autre Mac avec Codex")
    assert [r["id"] for r in found] == ["remote"]
    assert found[0]["device_name"] == "Work Mac"
    assert found[0]["project_name"] == "Sabai"
    assert found[0]["latest_user_message"] == "voice experiments"
    assert [r["id"] for r in tools.search_conversations("voice", device="current")] == ["local"]
    assert [r["id"] for r in tools.list_conversations(device="Work Mac")] == ["remote"]
    assert not tools.search_conversations("", project="Sabai", device="missing")
    assert not tools.search_conversations("voice", device="other", date_from="2099-01-01")
    assert tools.list_projects(query="ça bye")[0]["name"] == "Sabai"
    assert any(d["is_current"] and d["id"] == "local-device" for d in tools.list_devices())
