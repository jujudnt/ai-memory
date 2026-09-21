from __future__ import annotations

import hashlib
import json
import platform
import sqlite3
import socket
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aimemory.adapters.base import DiscoveredSource, SourceSession
from aimemory.config import default_codex_home
from aimemory.models import DeviceIdentity, Message, NormalizedConversation, ProjectIdentity, ToolCall
from aimemory.projects import identify_project
from aimemory.sources import codex_source
from aimemory.adapters.codex_messages import deduplicate_messages, NORMALIZATION_VERSION


class CodexAdapter:
    source = "codex"
    parser_version = 5

    def __init__(self, codex_home: Path | None = None, device_id: str | None = None):
        self.codex_home = codex_home or default_codex_home()
        self.thread_metadata = _load_thread_metadata(self.codex_home)
        self.codex_projects = _load_codex_projects(self.codex_home)
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

    def parse_session(self, path: Path, raw: bytes | None = None) -> NormalizedConversation:
        records = _read_jsonl(path, raw)
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
                "custom_tool_call",
                "web_search_call",
            }:
                name = _tool_name(item)
                arguments = _stringify(item.get("arguments", item.get("input", item.get("action"))))
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
                "custom_tool_call_output",
            }:
                call_id = str(item.get("call_id") or item.get("id") or f"output_{ordinal}")
                output = _stringify(item.get("output") or item)
                call_outputs[call_id] = output

        for call in tool_calls:
            if call.id in call_outputs:
                call.output = call_outputs[call.id]

        source_session_id = str(
            session_meta.get("id")
            or session_meta.get("session_id")
            or _session_id_from_path(path)
        )
        thread_meta = self.thread_metadata.get(source_session_id) or self.thread_metadata.get(str(path)) or {}
        created_at = session_meta.get("timestamp") or _timestamp(thread_meta.get("created_at")) or _first_timestamp(records)
        updated_at = _last_timestamp(records) or _timestamp(thread_meta.get("updated_at")) or created_at
        cwd = turn_context.get("cwd") or session_meta.get("cwd") or thread_meta.get("cwd")
        git = session_meta.get("git") if isinstance(session_meta.get("git"), dict) else {}
        git_remote = git.get("remote_url") or git.get("remote") or git.get("repository_url")
        git_branch = git.get("branch") or turn_context.get("git_branch")
        code_project = identify_project(cwd, git_remote, git_branch)
        codex_project = _identify_codex_project(cwd, thread_meta.get("project_id"), self.codex_projects)
        project = codex_project or code_project
        model = turn_context.get("model") or session_meta.get("model") or session_meta.get("model_provider") or thread_meta.get("model_provider")
        title = _title_from_messages(messages) or thread_meta.get("title")
        actual_source = codex_source(session_meta)

        return NormalizedConversation(
            schema_version=1,
            id=_conversation_id(self.source, source_session_id),
            source=actual_source,
            source_session_id=source_session_id,
            created_at=created_at,
            updated_at=updated_at,
            device=self.device,
            project=project,
            model=model,
            title=title,
            messages=deduplicate_messages(messages),
            tool_calls=tool_calls,
            files_referenced=sorted(files),
            commands=commands,
            metadata={
                "source_path": str(path),
                "raw_record_count": len(records),
                "codex_cli_version": session_meta.get("cli_version"),
                "history_mode": session_meta.get("history_mode"),
                "parser_version": self.parser_version,
                "message_normalization_version": NORMALIZATION_VERSION,
                "codex_thread_source": thread_meta.get("source"),
                "codex_project": asdict(codex_project) if codex_project else None,
                "code_project": asdict(code_project) if code_project else None,
                "session_metadata": session_meta,
            },
        )

    def identify_project(self, conversation: NormalizedConversation):
        return conversation.project


def _read_jsonl(path: Path, raw: bytes | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    lines = (raw if raw is not None else path.read_bytes()).splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            # A live session may end in a partially written record, never skip corruption inside it.
            if index == len(lines) - 1:
                continue
            raise ValueError(f"Invalid JSON in {path.name}, line {index + 1}")
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


def _load_thread_metadata(codex_home: Path) -> dict[str, dict[str, Any]]:
    db_path = codex_home / "state_5.sqlite"
    if not db_path.exists():
        return {}
    conn = None
    try:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(threads)").fetchall()
        }
        desired = [
            "id",
            "rollout_path",
            "source",
            "model_provider",
            "cwd",
            "title",
            "created_at",
            "updated_at",
            "project_id",
        ]
        select_columns = [column for column in desired if column in columns]
        rows = conn.execute(
            f"SELECT {', '.join(select_columns)} FROM threads"
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        if conn is not None:
            conn.close()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        data = dict(row)
        if data.get("id"):
            result[str(data["id"])] = data
        if data.get("rollout_path"):
            result[str(data["rollout_path"])] = data
    return result


def _load_codex_projects(codex_home: Path) -> dict[str, dict[str, Any]]:
    db_path = codex_home / "state_5.sqlite"
    if not db_path.exists():
        return {}
    conn = None
    try:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        project_rows = conn.execute("SELECT id, name, metadata FROM projects").fetchall()
        root_rows = conn.execute("SELECT project_id, path FROM project_roots ORDER BY position").fetchall()
    except sqlite3.Error:
        return {}
    finally:
        if conn is not None:
            conn.close()

    projects: dict[str, dict[str, Any]] = {
        str(row["id"]): {"id": str(row["id"]), "name": str(row["name"]), "metadata": row["metadata"], "roots": []}
        for row in project_rows
    }
    for row in root_rows:
        project = projects.get(str(row["project_id"]))
        if project and isinstance(row["path"], str):
            project["roots"].append(row["path"])
    return projects


def _identify_codex_project(
    cwd: str | None,
    project_id: Any,
    projects: dict[str, dict[str, Any]],
) -> ProjectIdentity | None:
    if project_id and str(project_id) in projects:
        return _codex_project_identity(projects[str(project_id)])
    if not cwd:
        return None
    try:
        cwd_path = Path(cwd).expanduser().resolve()
    except OSError:
        cwd_path = Path(cwd).expanduser()
    best: tuple[int, dict[str, Any], str] | None = None
    for project in projects.values():
        for root in project.get("roots", []):
            try:
                root_path = Path(root).expanduser().resolve()
            except OSError:
                root_path = Path(root).expanduser()
            if cwd_path == root_path or root_path in cwd_path.parents:
                length = len(str(root_path))
                if best is None or length > best[0]:
                    best = (length, project, str(root_path))
    if best:
        return _codex_project_identity(best[1], root=best[2])
    return None


def _codex_project_identity(project: dict[str, Any], root: str | None = None) -> ProjectIdentity:
    roots = project.get("roots") if isinstance(project.get("roots"), list) else []
    cwd = root or (roots[0] if roots else None)
    return ProjectIdentity(
        id=f"codex_project_{project['id']}",
        name=str(project["name"]),
        cwd=cwd,
        git_remote=None,
        git_branch=None,
    )


def _timestamp(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and value > 0:
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, UTC).isoformat()
    return None


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
            if message.content.lstrip().startswith(("<environment_context>", "<recommended_plugins>", "# AGENTS.md")):
                continue
            title = " ".join(message.content.split())
            return title[:120]
    return None
