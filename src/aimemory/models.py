from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


Role = Literal["system", "developer", "user", "assistant", "tool", "unknown"]


@dataclass(slots=True)
class DeviceIdentity:
    id: str
    name: str


@dataclass(slots=True)
class ProjectIdentity:
    id: str
    name: str
    cwd: str | None = None
    git_remote: str | None = None
    git_branch: str | None = None


@dataclass(slots=True)
class Message:
    id: str
    role: Role
    content: str
    timestamp: str | None = None
    ordinal: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str | None = None
    output: str | None = None
    timestamp: str | None = None
    ordinal: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedConversation:
    schema_version: int
    id: str
    source: str
    source_session_id: str
    created_at: str | None
    updated_at: str | None
    device: DeviceIdentity
    project: ProjectIdentity | None
    model: str | None
    title: str | None
    messages: list[Message] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    files_referenced: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NormalizedConversation":
        device = DeviceIdentity(**data["device"])
        project_data = data.get("project")
        project = ProjectIdentity(**project_data) if project_data else None
        messages = [Message(**item) for item in data.get("messages", [])]
        tool_calls = [ToolCall(**item) for item in data.get("tool_calls", [])]
        return cls(
            schema_version=data["schema_version"],
            id=data["id"],
            source=data["source"],
            source_session_id=data["source_session_id"],
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            device=device,
            project=project,
            model=data.get("model"),
            title=data.get("title"),
            messages=messages,
            tool_calls=tool_calls,
            files_referenced=list(data.get("files_referenced", [])),
            commands=list(data.get("commands", [])),
            metadata=dict(data.get("metadata", {})),
        )

    def searchable_text(self) -> str:
        parts: list[str] = []
        if self.title:
            parts.append(self.title)
        if self.project:
            parts.extend(
                item
                for item in (
                    self.project.name,
                    self.project.cwd,
                    self.project.git_remote,
                    self.project.git_branch,
                )
                if item
            )
        parts.extend(self.files_referenced)
        parts.extend(self.commands)
        parts.extend(message.content for message in self.messages if message.content)
        parts.extend(call.arguments or "" for call in self.tool_calls)
        parts.extend(call.output or "" for call in self.tool_calls)
        return "\n".join(part for part in parts if part)
