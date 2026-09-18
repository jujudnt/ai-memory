from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    home: Path
    archive: Path
    db: Path
    vectors: Path
    cache: Path
    logs: Path
    state: Path

    @classmethod
    def from_env(cls) -> "AppPaths":
        home = Path(os.environ.get("AI_MEMORY_HOME", "~/.ai-memory")).expanduser()
        return cls(
            home=home,
            archive=home / "archive",
            db=home / "db",
            vectors=home / "vectors",
            cache=home / "cache",
            logs=home / "logs",
            state=home / "state",
        )

    def ensure(self) -> None:
        for path in (
            self.home,
            self.archive,
            self.db,
            self.vectors,
            self.cache,
            self.logs,
            self.state,
        ):
            path.mkdir(parents=True, exist_ok=True)


def default_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
