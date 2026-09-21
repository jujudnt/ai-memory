import json
import sqlite3
from pathlib import Path

from aimemory.adapters import CodexAdapter
from aimemory.adapters.common import conversation_id


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")


def test_codex_adapter_parses_enveloped_records(tmp_path):
    session = tmp_path / ".codex" / "archived_sessions" / "rollout-test.jsonl"
    write_jsonl(
        session,
        [
            {
                "timestamp": "2026-09-18T00:00:00Z",
                "ordinal": 0,
                "type": "session_meta",
                "payload": {
                    "session_id": "session-1",
                    "timestamp": "2026-09-18T00:00:00Z",
                    "cwd": "/work/project",
                    "git": {
                        "remote_url": "git@github.com:User/Project.git",
                        "branch": "main",
                    },
                },
            },
            {
                "timestamp": "2026-09-18T00:01:00Z",
                "ordinal": 1,
                "type": "turn_context",
                "payload": {"cwd": "/work/project", "model": "codex-test"},
            },
            {
                "timestamp": "2026-09-18T00:02:00Z",
                "ordinal": 2,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "id": "m1",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Fix app.py Cloud Run error"}],
                },
            },
            {
                "timestamp": "2026-09-18T00:03:00Z",
                "ordinal": 3,
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "id": "fc1",
                    "name": "exec_command",
                    "arguments": "{\"cmd\":\"pytest\"}",
                    "call_id": "call-1",
                },
            },
            {
                "timestamp": "2026-09-18T00:04:00Z",
                "ordinal": 4,
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "id": "out1",
                    "call_id": "call-1",
                    "output": "1 passed",
                },
            },
        ],
    )

    conversation = CodexAdapter(tmp_path / ".codex", device_id="device-1").parse_session(session)

    assert conversation.source == "codex"
    assert conversation.source_session_id == "session-1"
    assert conversation.project is not None
    assert conversation.project.git_remote == "github.com/user/project"
    assert conversation.model == "codex-test"
    assert conversation.messages[0].content == "Fix app.py Cloud Run error"
    assert conversation.commands == ["pytest"]
    assert conversation.tool_calls[0].output == "1 passed"


def test_codex_adapter_prefers_codex_sidebar_project_root(tmp_path):
    codex_home = tmp_path / ".codex"
    session = codex_home / "sessions" / "rollout-project.jsonl"
    write_jsonl(
        session,
        [
            {
                "type": "session_meta",
                "payload": {
                    "session_id": "project-session",
                    "timestamp": "2026-09-18T00:00:00Z",
                },
            },
            {"type": "turn_context", "payload": {"cwd": "/Users/julia/Documents/Perso/ai-memory"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": "hello",
                },
            },
        ],
    )
    db_path = codex_home / "state_5.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT,
                source TEXT,
                model_provider TEXT,
                cwd TEXT,
                title TEXT,
                created_at INTEGER,
                updated_at INTEGER,
                project_id TEXT
            );
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE project_roots (
                project_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                path TEXT NOT NULL
            );
            """
        )
        conn.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-perso", "Perso"))
        conn.execute(
            "INSERT INTO project_roots(project_id, position, path) VALUES (?, ?, ?)",
            ("project-perso", 0, "/Users/julia/Documents/Perso"),
        )

    conversation = CodexAdapter(codex_home, device_id="device-1").parse_session(session)

    assert conversation.project is not None
    assert conversation.project.id == "codex_project_project-perso"
    assert conversation.project.name == "Perso"
    assert conversation.metadata["codex_project"]["name"] == "Perso"
    assert conversation.metadata["code_project"]["name"] == "ai-memory"


def test_codex_adapter_labels_vscode_threads_from_explicit_originator(tmp_path):
    codex_home = tmp_path / ".codex"
    session = codex_home / "sessions" / "rollout-vscode-session.jsonl"
    write_jsonl(
        session,
        [
            {
                "timestamp": "2026-09-18T00:00:00Z",
                "type": "session_meta",
                "payload": {"session_id": "vscode-session", "timestamp": "2026-09-18T00:00:00Z", "originator": "codex_vscode"},
            },
            {
                "timestamp": "2026-09-18T00:01:00Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "id": "m1",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Depuis VS Code"}],
                },
            },
        ],
    )
    db_path = codex_home / "state_5.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                source TEXT NOT NULL,
                model_provider TEXT NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "vscode-session",
                str(session),
                1_789_000_000,
                1_789_000_060,
                "vscode",
                "openai",
                "/tmp/project",
                "Thread VS Code",
            ),
        )

    conversation = CodexAdapter(codex_home, device_id="device-1").parse_session(session)

    assert conversation.id == conversation_id("codex", "vscode-session")
    assert conversation.source == "vscode-codex"
    assert conversation.metadata["codex_thread_source"] == "vscode"
