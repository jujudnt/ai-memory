from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aimemory.adapters.base import DiscoveredSource, SourceSession
from aimemory.adapters.common import conversation_id, device_identity, extract_text, read_jsonl, title_from_messages
from aimemory.models import Message, NormalizedConversation
from aimemory.projects import identify_project


class VSCodeAdapter:
    source = "vscode"
    parser_version = 1

    def __init__(self, code_user_dir: Path | None = None, device_id: str | None = None):
        self.code_user_dir = code_user_dir or _default_code_user_dir()
        self.device = device_identity(device_id)

    def discover(self) -> list[DiscoveredSource]:
        roots = [
            self.code_user_dir / "workspaceStorage",
            self.code_user_dir / "globalStorage" / "emptyWindowChatSessions",
        ]
        return [DiscoveredSource(self.source, root, root.exists()) for root in roots]

    def watch_paths(self) -> list[Path]:
        return [item.root for item in self.discover() if item.exists]

    def scan_sessions(self) -> list[SourceSession]:
        sessions: list[SourceSession] = []
        for root in self.watch_paths():
            pattern = "**/chatSessions/*.jsonl" if root.name == "workspaceStorage" else "*.jsonl"
            for path in sorted(root.glob(pattern)):
                stat = path.stat()
                sessions.append(SourceSession(self.source, path, path.stem, stat.st_size, stat.st_mtime))
        return sessions

    def parse_session(self, path: Path, raw: bytes | None = None) -> NormalizedConversation:
        records = read_jsonl(path, raw)
        messages: list[Message] = []
        created_at = None
        updated_at = None
        source_session_id = path.stem
        cwd = _workspace_folder(path)

        for record_index, record in enumerate(records):
            payload = record.get("v") if isinstance(record.get("v"), dict) else record
            if isinstance(payload.get("sessionId"), str):
                source_session_id = payload["sessionId"]
            created_at = created_at or _timestamp(payload.get("creationDate") or payload.get("createdAt"))
            updated_at = _timestamp(payload.get("lastMessageDate") or payload.get("updatedAt")) or updated_at
            for request in payload.get("requests") or []:
                if not isinstance(request, dict):
                    continue
                message_text = _request_text(request)
                if message_text:
                    messages.append(
                        Message(
                            id=str(request.get("id") or f"request_{record_index}_{len(messages)}"),
                            role="user",
                            content=message_text,
                            timestamp=_timestamp(request.get("timestamp") or request.get("time")),
                            ordinal=len(messages),
                            metadata={"source_type": "request"},
                        )
                    )
                response_text = _response_text(request)
                if response_text:
                    messages.append(
                        Message(
                            id=str(request.get("responseId") or f"response_{record_index}_{len(messages)}"),
                            role="assistant",
                            content=response_text,
                            timestamp=_timestamp(request.get("responseTimestamp") or request.get("timestamp")),
                            ordinal=len(messages),
                            metadata={"source_type": "response"},
                        )
                    )

        if not messages:
            raise ValueError("VS Code chat session is empty")
        return NormalizedConversation(
            schema_version=1,
            id=conversation_id(self.source, source_session_id),
            source=self.source,
            source_session_id=source_session_id,
            created_at=created_at,
            updated_at=updated_at or created_at,
            device=self.device,
            project=identify_project(cwd, None, None),
            model=None,
            title=title_from_messages(messages),
            messages=messages,
            tool_calls=[],
            files_referenced=[],
            commands=[],
            metadata={
                "source_path": str(path),
                "raw_record_count": len(records),
                "parser_version": self.parser_version,
            },
        )

    def identify_project(self, conversation: NormalizedConversation):
        return conversation.project


def _default_code_user_dir() -> Path:
    home = Path.home()
    if (home / "Library" / "Application Support" / "Code" / "User").exists():
        return home / "Library" / "Application Support" / "Code" / "User"
    if (home / "AppData" / "Roaming" / "Code" / "User").exists():
        return home / "AppData" / "Roaming" / "Code" / "User"
    return home / ".config" / "Code" / "User"


def _timestamp(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and value > 0:
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, UTC).isoformat()
    return None


def _workspace_folder(path: Path) -> str | None:
    workspace = path.parent.parent / "workspace.json"
    try:
        data = json.loads(workspace.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    folder = data.get("folder") or data.get("workspace")
    if isinstance(folder, str) and folder.startswith("file://"):
        return folder.removeprefix("file://")
    return folder if isinstance(folder, str) else None


def _request_text(request: dict[str, Any]) -> str:
    for key in ("message", "prompt", "text", "input", "request"):
        text = extract_text(request.get(key))
        if text:
            return text
    return ""


def _response_text(request: dict[str, Any]) -> str:
    response = request.get("response") or request.get("result") or request.get("answer")
    if isinstance(response, list):
        return "\n".join(part for item in response if (part := extract_text(item)))
    if isinstance(response, dict):
        return extract_text(response.get("value") or response.get("content") or response.get("message"))
    return extract_text(response)
