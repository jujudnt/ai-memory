from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from aimemory.models import NormalizedConversation, ProjectIdentity


@dataclass(frozen=True, slots=True)
class DiscoveredSource:
    source: str
    root: Path
    exists: bool


@dataclass(frozen=True, slots=True)
class SourceSession:
    source: str
    path: Path
    source_session_id: str
    size: int
    mtime: float


class ConversationSourceAdapter(Protocol):
    source: str

    def discover(self) -> list[DiscoveredSource]:
        ...

    def scan_sessions(self) -> list[SourceSession]:
        ...

    def parse_session(self, path: Path) -> NormalizedConversation:
        ...

    def watch_paths(self) -> list[Path]:
        ...

    def identify_project(
        self, conversation: NormalizedConversation
    ) -> ProjectIdentity | None:
        ...
