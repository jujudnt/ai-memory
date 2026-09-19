import json

import pytest

from aimemory.adapters.claude import ClaudeAdapter
from aimemory.adapters.vscode import VSCodeAdapter
from aimemory.config import AppPaths
from aimemory.service import MemoryService


def test_import_codex_and_search(tmp_path):
    codex_home = tmp_path / "codex"
    session = codex_home / "sessions" / "2026" / "09" / "18" / "session.jsonl"
    session.parent.mkdir(parents=True)
    records = [
        {
            "timestamp": "2026-09-18T00:00:00Z",
            "ordinal": 0,
            "type": "session_meta",
            "payload": {"session_id": "abc", "cwd": "/repo/ai-memory"},
        },
        {
            "timestamp": "2026-09-18T00:01:00Z",
            "ordinal": 1,
            "type": "response_item",
            "payload": {
                "type": "message",
                "id": "m1",
                "role": "user",
                "content": [{"type": "input_text", "text": "We configured Cloud Armor for an endpoint"}],
            },
        },
    ]
    session.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")

    paths = AppPaths(
        home=tmp_path / "home",
        archive=tmp_path / "home" / "archive",
        db=tmp_path / "home" / "db",
        vectors=tmp_path / "home" / "vectors",
        cache=tmp_path / "home" / "cache",
        logs=tmp_path / "home" / "logs",
        state=tmp_path / "home" / "state",
    )
    service = MemoryService(paths)

    result = service.import_codex(codex_home=codex_home)
    rows = service.search("Cloud Armor")

    assert result.scanned == 1
    assert result.imported == 1
    assert len(rows) == 1
    assert rows[0]["source_session_id"] == "abc"
    assert service.status()["archive_count"] == 1


def test_recent_conversations_show_latest_user_request(tmp_path):
    codex_home = tmp_path / "codex"
    session = codex_home / "sessions" / "2026" / "09" / "18" / "session.jsonl"
    session.parent.mkdir(parents=True)
    records = [
        {
            "timestamp": "2026-09-18T00:00:00Z",
            "ordinal": 0,
            "type": "session_meta",
            "payload": {"session_id": "abc", "cwd": "/repo/ai-memory"},
        },
        {
            "timestamp": "2026-09-18T00:01:00Z",
            "ordinal": 1,
            "type": "response_item",
            "payload": {"type": "message", "id": "m1", "role": "user", "content": "Old opening prompt"},
        },
        {
            "timestamp": "2026-09-18T00:02:00Z",
            "ordinal": 2,
            "type": "response_item",
            "payload": {"type": "message", "id": "m2", "role": "assistant", "content": "ok"},
        },
        {
            "timestamp": "2026-09-18T00:03:00Z",
            "ordinal": 3,
            "type": "response_item",
            "payload": {
                "type": "message",
                "id": "m3",
                "role": "user",
                "content": "# Files mentioned by the user:\n\n## screenshot.png\n\n## My request:\nShow this latest request",
            },
        },
    ]
    session.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")

    paths = AppPaths(
        home=tmp_path / "home",
        archive=tmp_path / "home" / "archive",
        db=tmp_path / "home" / "db",
        vectors=tmp_path / "home" / "vectors",
        cache=tmp_path / "home" / "cache",
        logs=tmp_path / "home" / "logs",
        state=tmp_path / "home" / "state",
    )
    service = MemoryService(paths)

    service.import_codex(codex_home=codex_home)
    recent = service.list_conversations(limit=1)[0]

    assert recent["title"] == "Old opening prompt"
    assert recent["latest_user_message"] == "Show this latest request"


def test_claude_adapter_imports_jsonl_project_session(tmp_path):
    claude_home = tmp_path / "claude"
    session = claude_home / "projects" / "-tmp-project" / "claude-session.jsonl"
    session.parent.mkdir(parents=True)
    records = [
        {
            "type": "user",
            "sessionId": "claude-session",
            "timestamp": "2026-09-18T10:00:00Z",
            "cwd": "/tmp/project",
            "gitBranch": "main",
            "message": {"role": "user", "content": "Peux-tu relire ce projet ?"},
        },
        {
            "type": "assistant",
            "sessionId": "claude-session",
            "timestamp": "2026-09-18T10:01:00Z",
            "message": {"role": "assistant", "content": "Oui."},
        },
    ]
    session.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")

    conversation = ClaudeAdapter(claude_home=claude_home).parse_session(session)

    assert conversation.source == "claude"
    assert conversation.source_session_id == "claude-session"
    assert conversation.title == "Peux-tu relire ce projet ?"
    assert [message.role for message in conversation.messages] == ["user", "assistant"]


def test_claude_adapter_labels_vscode_entrypoint(tmp_path):
    claude_home = tmp_path / "claude"
    session = claude_home / "projects" / "-tmp-project" / "claude-vscode-session.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": "claude-vscode-session",
                "timestamp": "2026-09-18T10:00:00Z",
                "cwd": "/tmp/project",
                "entrypoint": "claude-vscode",
                "message": {"role": "user", "content": "Session depuis VS Code"},
            }
        ),
        encoding="utf-8",
    )

    conversation = ClaudeAdapter(claude_home=claude_home).parse_session(session)

    assert conversation.source == "vscode-claude"
    assert conversation.metadata["claude_entrypoint"] == "claude-vscode"


def test_vscode_adapter_imports_non_empty_chat_session(tmp_path):
    code_user = tmp_path / "Code" / "User"
    session = code_user / "workspaceStorage" / "abc" / "chatSessions" / "vscode-session.jsonl"
    session.parent.mkdir(parents=True)
    (session.parent.parent / "workspace.json").write_text(json.dumps({"folder": "file:///tmp/project"}))
    payload = {
        "kind": 0,
        "v": {
            "version": 3,
            "creationDate": 1_789_000_000_000,
            "sessionId": "vscode-session",
            "requests": [
                {
                    "id": "r1",
                    "message": {"text": "Explique cette erreur"},
                    "response": [{"value": "Voici la cause."}],
                }
            ],
        },
    }
    session.write_text(json.dumps(payload), encoding="utf-8")

    conversation = VSCodeAdapter(code_user_dir=code_user).parse_session(session)

    assert conversation.source == "vscode"
    assert conversation.title == "Explique cette erreur"
    assert [message.role for message in conversation.messages] == ["user", "assistant"]


def test_vscode_adapter_skips_empty_chat_session(tmp_path):
    code_user = tmp_path / "Code" / "User"
    session = code_user / "workspaceStorage" / "abc" / "chatSessions" / "empty.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text(json.dumps({"kind": 0, "v": {"sessionId": "empty", "requests": []}}))

    with pytest.raises(ValueError, match="empty"):
        VSCodeAdapter(code_user_dir=code_user).parse_session(session)
