from __future__ import annotations

import hashlib
import json
import platform
import socket
import uuid
from pathlib import Path
from typing import Any

from aimemory.adapters.base import DiscoveredSource, SourceSession
from aimemory.config import default_codex_home
from aimemory.models import DeviceIdentity, Message, NormalizedConversation, ToolCall
from aimemory.projects import identify_project


class CodexAdapter:
    source = "codex"

    def __init__(self, codex_home: Path | None = None, device_id: str | None = None):
        self.codex_home = codex_home or default_codex_home()
        self.device = DeviceIdentity(
            id=device_id or _stable_device_id(),
            name=socket.gethostname() or platform.node() or "unknown-device",
        )

    def discover(self) -> list[DiscoveredSource]:
        return [
            DiscoveredSource(self.source, self.codex_home / "sessions", (self.codex_home / "sessions").exists()),
            DiscoveredSource(
                self.source,
                self.codex_home / "archived_sessions",
                (self.codex_home / "archived_sessions").exists(),
            ),
        ]

    def watch_paths(self) -> list[Path]:
        return [item.root for item in self.discover() if item.exists]

    def scan_sessions(self) -> list[SourceSession]:
        sessions: list[SourceSession] = []
        for root in self.watch_paths():
            for path in sorted(root.rglob("*.jsonl")):
                stat = path.stat()
                sessions.append(
                    SourceSession(
                        source=self.source,
                        path=path,
                        source_session_id=_session_id_from_path(path),
                        size=stat.st_size,
                        mtime=stat.st_mtime,
                    )
                )
        return sessions

    def parse_session(self, path: Path) -> NormalizedConversation:
        records = _read_jsonl(path)
        session_meta: dict[str, Any] = {}
        turn_context: dict[str, Any] = {}
        messages: list[Message] = []
        tool_calls: list[ToolCall] = []
        files: set[str] = set()
        commands: list[str] = []
        call_outputs: dict[str, str] = {}

        for ordinal, record in enumerate(records):
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else record
            record_type = record.get("type") or payload.get("type")
            timestamp = record.get("timestamp") or payload.get("timestamp")

            if record_type == "session_meta":
                session_meta.update(payload)
                continue
            if record_type == "turn_context":
                turn_context.update(payload)
                continue

            item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
            item_type = item.get("type") or record_type
            role = _role_from_item(item)

            if item_type in {"message", "UserMessage", "AgentMessage"} or role in {
                "user",
                "assistant",
                "developer",
                "system",
                "tool",
            }:
                text = _extract_text(item.get("content"))
                if text:
                    message_id = str(item.get("id") or f"msg_{ordinal}")
                    messages.append(
                        Message(
                            id=message_id,
                            role=role,
                            content=text,
                            timestamp=timestamp,
                            ordinal=int(record.get("ordinal", ordinal)),
                            metadata={"source_type": item_type},
                        )
                    )
                    files.update(_extract_file_mentions(text))

            if item_type in {
                "function_call",
                "tool_call",
                "tool_search_call",
                "computer_call",
            }:
                name = _tool_name(item)
                arguments = _stringify(item.get("arguments"))
                call_id = str(item.get("call_id") or item.get("id") or f"call_{ordinal}")
                tool_calls.append(
                    ToolCall(
                        id=call_id,
                        name=name,
                        arguments=arguments,
                        timestamp=timestamp,
                        ordinal=int(record.get("ordinal", ordinal)),
                        metadata={"source_type": item_type},
                    )
                )
                command = _command_from_arguments(name, arguments)
                if command:
                    commands.append(command)

            if item_type in {
                "function_call_output",
                "tool_call_output",
                "tool_search_output",
            }:
                call_id = str(item.get("call_id") or item.get("id") or f"output_{ordinal}")
                output = _stringify(item.get("output") or item)
                call_outputs[call_id] = output

        for call in tool_calls:
            if call.id in call_outputs:
                call.output = call_outputs[call.id]

        source_session_id = str(
            session_meta.get("session_id")
            or session_meta.get("id")
            or _session_id_from_path(path)
        )
        created_at = session_meta.get("timestamp") or _first_timestamp(records)
        updated_at = _last_timestamp(records) or created_at
        cwd = turn_context.get("cwd") or session_meta.get("cwd")
        git = session_meta.get("git") if isinstance(session_meta.get("git"), dict) else {}
        git_remote = git.get("remote_url") or git.get("remote") or git.get("repository_url")
        git_branch = git.get("branch") or turn_context.get("git_branch")
        project = identify_project(cwd, git_remote, git_branch)
        model = turn_context.get("model") or session_meta.get("model") or session_meta.get("model_provider")
        title = _title_from_messages(messages)

        return NormalizedConversation(
            schema_version=1,
            id=_conversation_id(self.source, source_session_id),
            source=self.source,
            source_session_id=source_session_id,
            created_at=created_at,
            updated_at=updated_at,
            device=self.device,
            project=project,
            model=model,
            title=title,
            messages=messages,
            tool_calls=tool_calls,
            files_referenced=sorted(files),
            commands=commands,
            metadata={
                "source_path": str(path),
                "raw_record_count": len(records),
                "codex_cli_version": session_meta.get("cli_version"),
                "history_mode": session_meta.get("history_mode"),
            },
        )

    def identify_project(self, conversation: NormalizedConversation):
        return conversation.project


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    return records


def _stable_device_id() -> str:
    seed = f"{socket.gethostname()}:{platform.node()}:{platform.system()}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _session_id_from_path(path: Path) -> str:
    return path.stem


def _conversation_id(source: str, source_session_id: str) -> str:
    digest = hashlib.sha256(f"{source}:{source_session_id}".encode("utf-8")).hexdigest()
    return str(uuid.UUID(digest[:32]))


def _first_timestamp(records: list[dict[str, Any]]) -> str | None:
    for record in records:
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str):
            return timestamp
    return None


def _last_timestamp(records: list[dict[str, Any]]) -> str | None:
    for record in reversed(records):
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str):
            return timestamp
    return None


def _role_from_item(item: dict[str, Any]) -> str:
    role = item.get("role")
    if role in {"system", "developer", "user", "assistant", "tool"}:
        return role
    item_type = item.get("type")
    if item_type == "UserMessage":
        return "user"
    if item_type == "AgentMessage":
        return "assistant"
    return "unknown"


def _extract_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or item.get("output_text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    if isinstance(content, dict):
        text = content.get("text") or content.get("content")
        return text if isinstance(text, str) else ""
    return ""


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _tool_name(item: dict[str, Any]) -> str:
    namespace = item.get("namespace")
    name = item.get("name") or item.get("tool_name") or item.get("type") or "unknown_tool"
    if namespace:
        return f"{namespace}.{name}"
    return str(name)


def _command_from_arguments(name: str, arguments: str | None) -> str | None:
    if not arguments:
        return None
    if name not in {"exec_command", "functions.exec_command"} and not name.endswith(".exec_command"):
        return None
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return None
    command = parsed.get("cmd") if isinstance(parsed, dict) else None
    return command if isinstance(command, str) else None


def _extract_file_mentions(text: str) -> set[str]:
    mentions: set[str] = set()
    for token in text.replace("`", " ").split():
        cleaned = token.strip(".,:;()[]{}<>\"'")
        if "/" in cleaned and len(cleaned) < 260:
            suffix = Path(cleaned).suffix
            if suffix or cleaned.startswith("/"):
                mentions.add(cleaned)
    return mentions


def _title_from_messages(messages: list[Message]) -> str | None:
    for message in messages:
        if message.role == "user" and message.content.strip():
            title = " ".join(message.content.split())
            return title[:120]
    return None
