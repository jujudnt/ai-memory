from __future__ import annotations

import hashlib
import json
import io
import platform
import socket
import uuid
from pathlib import Path
from typing import Any

from aimemory.models import DeviceIdentity


def stable_device_id() -> str:
    seed = f"{socket.gethostname()}:{platform.node()}:{platform.system()}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def device_identity(device_id: str | None = None) -> DeviceIdentity:
    return DeviceIdentity(
        id=device_id or stable_device_id(),
        name=socket.gethostname() or platform.node() or "unknown-device",
    )


def conversation_id(source: str, source_session_id: str) -> str:
    digest = hashlib.sha256(f"{source}:{source_session_id}".encode("utf-8")).hexdigest()
    return str(uuid.UUID(digest[:32]))


def iter_jsonl(path: Path, raw=None, offset=0, state=None):
    owned = not hasattr(raw, "stream")
    stream = (path.open("rb") if raw is None else io.BytesIO(raw)) if owned else raw.stream
    try:
        stream.seek(offset)
        line = stream.readline()
        index = 0
        while line:
            following = stream.readline()
            index += 1
            if line.strip():
                try:
                    value = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    if following:
                        raise ValueError(f"Invalid JSON in {path.name}, line {index}")
                    if state is not None:
                        state["partial"] = True
                else:
                    if isinstance(value, dict):
                        yield value
            line = following
    finally:
        if owned:
            stream.close()


def read_jsonl(path: Path, raw: bytes | None = None) -> list[dict[str, Any]]:
    return list(iter_jsonl(path, raw))


def stringify(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def extract_text(content: Any) -> str:
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
        text = content.get("text") or content.get("content") or content.get("value")
        return text if isinstance(text, str) else ""
    return ""


def first_timestamp(records: list[dict[str, Any]]) -> str | None:
    for record in records:
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str):
            return timestamp
    return None


def last_timestamp(records: list[dict[str, Any]]) -> str | None:
    for record in reversed(records):
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str):
            return timestamp
    return None


def extract_file_mentions(text: str) -> set[str]:
    mentions: set[str] = set()
    for token in text.replace("`", " ").split():
        cleaned = token.strip(".,:;()[]{}<>\"'")
        if "/" in cleaned and len(cleaned) < 260:
            suffix = Path(cleaned).suffix
            if suffix or cleaned.startswith("/"):
                mentions.add(cleaned)
    return mentions


def title_from_messages(messages) -> str | None:
    for message in messages:
        if message.role == "user" and message.content.strip():
            if message.content.lstrip().startswith(("<environment_context>", "<recommended_plugins>", "# AGENTS.md")):
                continue
            title = " ".join(message.content.split())
            return title[:120]
    return None
