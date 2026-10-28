"""Native change notifications, with periodic discovery as a safety net."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from aimemory.adapters.vscode import VSCodeAdapter
from aimemory.config import default_codex_home


class SourceEvents:
    def __init__(self, state: Path, codex_home: Path | None = None):
        self.changed = threading.Event()
        self.requested = threading.Event()
        self.state = state.resolve()
        self.codex_home = (codex_home or default_codex_home()).resolve()
        self.claude_home = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser().resolve()
        self.observer = None
        self.roots = set()

    def notify(self, event) -> None:
        # Reads must not wake the collector (Linux also reports opened/closed).
        if event.event_type not in {"created", "modified", "deleted", "moved"}:
            return
        for name in (event.src_path, getattr(event, "dest_path", "")):
            if not name:
                continue
            path = Path(name)
            if path == self.state:
                continue
            if path.parent == self.state:
                if path.name in {"sync-request.json", "sync-preferences.json", "cloud.json"}:
                    self.requested.set()
                continue
            if event.is_directory or path.suffix in {".jsonl", ".json"} or path.name in {
                "state_5.sqlite", "state_5.sqlite-wal",
            }:
                self.changed.set()

    def start(self) -> bool:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
            handler = FileSystemEventHandler()
            handler.on_any_event = self.notify
            self.handler = handler
            self.observer = Observer()
            self.refresh()
            self.observer.start()
            return True
        except (ImportError, OSError, RuntimeError):
            self.close()
            return False

    def refresh(self) -> None:
        # Watching only source folders avoids reacting to the app's own logs/index.
        targets = [(self.state, False), (self.codex_home, False), (self.claude_home, False)]
        targets.extend((self.codex_home / name, True) for name in ("sessions", "archived_sessions"))
        targets.append((self.claude_home / "projects", True))
        targets.extend((item.root.resolve(), True) for item in VSCodeAdapter().discover())
        for path, recursive in targets:
            if path not in self.roots and path.is_dir():
                self.observer.schedule(self.handler, str(path), recursive=recursive)
                self.roots.add(path)

    @property
    def alive(self) -> bool:
        return bool(self.observer and self.observer.is_alive()
                    and all(emitter.is_alive() for emitter in self.observer.emitters))

    def close(self) -> None:
        if self.observer:
            self.observer.stop()
            if self.observer.is_alive():
                self.observer.join(timeout=5)
