import json
from pathlib import Path

from aimemory.adapters import CodexAdapter


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
