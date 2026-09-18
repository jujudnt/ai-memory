from __future__ import annotations

import time
from pathlib import Path


class Debouncer:
    def __init__(self, delay_seconds: float = 1.5):
        self.delay_seconds = delay_seconds
        self._dirty: dict[Path, float] = {}

    def mark_dirty(self, path: Path) -> None:
        self._dirty[path] = time.monotonic()

    def ready(self) -> list[Path]:
        now = time.monotonic()
        ready = [
            path
            for path, marked_at in self._dirty.items()
            if now - marked_at >= self.delay_seconds
        ]
        for path in ready:
            self._dirty.pop(path, None)
        return ready
