from __future__ import annotations

import json
from pathlib import Path

from aimemory.adapters.base import DiscoveredSource, SourceSession
from aimemory.adapters.common import (
    conversation_id,
    device_identity,
    extract_file_mentions,
    extract_text,
    first_timestamp,
    last_timestamp,
    read_jsonl,
    stringify,
    title_from_messages,
)
from aimemory.models import Message, NormalizedConversation, ToolCall
from aimemory.projects import identify_project


class ClaudeAdapter:
    source = "claude"
    parser_version = 3

    def __init__(self, claude_home: Path | None = None, device_id: str | None = None):
        self.claude_home = claude_home or Path("~/.claude").expanduser()
        self.device = device_identity(device_id)

    def discover(self) -> list[DiscoveredSource]:
        root = self.claude_home / "projects"
        return [DiscoveredSource(self.source, root, root.exists())]

    def watch_paths(self) -> list[Path]:
        return [item.root for item in self.discover() if item.exists]

    def scan_sessions(self) -> list[SourceSession]:
        sessions: list[SourceSession] = []
        for root in self.watch_paths():
            for path in sorted(root.rglob("*.jsonl")):
                stat = path.stat()
                sessions.append(SourceSession(self.source, path, path.stem, stat.st_size, stat.st_mtime))
        return sessions

    def parse_session(self, path: Path, raw: bytes | None = None) -> NormalizedConversation:
        records = read_jsonl(path, raw)
        messages: list[Message] = []
        tool_calls: list[ToolCall] = []
        files: set[str] = set()
        commands: list[str] = []
        source_session_id = path.stem
        cwd = None
        git_branch = None
        model = None
        entrypoint = None
        project_dir = _project_dir(path, self.claude_home)

        for ordinal, record in enumerate(records):
            if isinstance(record.get("sessionId"), str):
                source_session_id = record["sessionId"]
            cwd = cwd or record.get("cwd")
            git_branch = git_branch or record.get("gitBranch")
            model = model or record.get("model")
            entrypoint = entrypoint or record.get("entrypoint")
            message = record.get("message") if isinstance(record.get("message"), dict) else {}
            role = message.get("role") or record.get("type")
            if role not in {"user", "assistant", "system", "tool"}:
                role = "unknown"
            content = message.get("content")
            text = extract_text(content)
            if text:
                messages.append(
                    Message(
                        id=str(record.get("uuid") or message.get("id") or f"msg_{ordinal}"),
                        role=role,
                        content=text,
                        timestamp=record.get("timestamp"),
                        ordinal=ordinal,
                        metadata={"source_type": record.get("type")},
                    )
                )
                files.update(extract_file_mentions(text))
            for item in content if isinstance(content, list) else []:
                if not isinstance(item, dict) or item.get("type") != "tool_use":
                    continue
                name = str(item.get("name") or "claude_tool")
                arguments = stringify(item.get("input"))
                tool_calls.append(
                    ToolCall(
                        id=str(item.get("id") or f"tool_{ordinal}_{len(tool_calls)}"),
                        name=name,
                        arguments=arguments,
                        timestamp=record.get("timestamp"),
                        ordinal=ordinal,
                        metadata={"source_type": "tool_use"},
                    )
                )
                if name == "Bash" and isinstance(item.get("input"), dict) and isinstance(item["input"].get("command"), str):
                    commands.append(item["input"]["command"])

        cwd = cwd or _cwd_from_project_dir(project_dir)
        actual_source = _source_from_entrypoint(entrypoint)
        return NormalizedConversation(
            schema_version=1,
            id=conversation_id(self.source, source_session_id),
            source=actual_source,
            source_session_id=source_session_id,
            created_at=first_timestamp(records),
            updated_at=last_timestamp(records) or first_timestamp(records),
            device=self.device,
            project=identify_project(cwd, None, git_branch),
            model=model,
            title=_custom_title(path) or title_from_messages(messages),
            messages=messages,
            tool_calls=tool_calls,
            files_referenced=sorted(files),
            commands=commands,
            metadata={
                "source_path": str(path),
                "raw_record_count": len(records),
                "parser_version": self.parser_version,
                "claude_entrypoint": entrypoint,
                "claude_project_dir": project_dir,
                "claude_client": actual_source,
            },
        )

    def identify_project(self, conversation: NormalizedConversation):
        return conversation.project


def _custom_title(path: Path) -> str | None:
    title_path = path.with_suffix("") / "custom-title.json"
    try:
        data = json.loads(title_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    title = data.get("title") or data.get("customTitle") or data.get("name")
    return title[:120] if isinstance(title, str) and title.strip() else None


def _project_dir(path: Path, claude_home: Path) -> str | None:
    try:
        relative = path.relative_to(claude_home / "projects")
    except ValueError:
        return None
    return relative.parts[0] if relative.parts else None


def _cwd_from_project_dir(project_dir: str | None) -> str | None:
    if not project_dir or not project_dir.startswith("-"):
        return None
    value = "/" + project_dir[1:].replace("-", "/")
    return value or None


def _source_from_entrypoint(entrypoint: str | None) -> str:
    return {
        "claude-vscode": "vscode-claude",
        "claude-desktop": "claude-desktop",
        "claude-code": "claude-code",
        "claude-cli": "claude-code",
    }.get(entrypoint or "", "claude")
