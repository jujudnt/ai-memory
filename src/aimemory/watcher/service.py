from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from aimemory.service import MemoryService


@dataclass(slots=True)
class WatcherStatus:
    running: bool
    started_at: str
    last_scan_at: str | None = None
    last_success_at: str | None = None
    last_error_at: str | None = None
    last_error: str | None = None
    scanned: int = 0
    imported: int = 0
    skipped: int = 0
    total_scans: int = 0


class WatcherService:
    """Simple polling collector used until platform services are wired in.

    The desktop app can read the same status file to show whether collection is
    alive and when the last successful Codex import happened.
    """

    def __init__(self, service: MemoryService | None = None):
        self.service = service or MemoryService()
        self.status_path = self.service.paths.state / "watcher-status.json"
        self.status = WatcherStatus(running=False, started_at=_now())

    def run_polling(
        self,
        codex_home: Path | None = None,
        interval_seconds: float = 10.0,
        once: bool = False,
    ) -> None:
        self.status.running = True
        self.status.started_at = _now()
        self._write_status()
        try:
            while True:
                self.scan_once(codex_home)
                if once:
                    break
                time.sleep(interval_seconds)
        finally:
            self.status.running = False
            self._write_status()

    def scan_once(self, codex_home: Path | None = None) -> None:
        self.status.last_scan_at = _now()
        self.status.total_scans += 1
        try:
            result = self.service.import_codex(codex_home=codex_home)
            self.status.scanned = result.scanned
            self.status.imported = result.imported
            self.status.skipped = result.skipped
            self.status.last_success_at = _now()
            self.status.last_error = None
            self.status.last_error_at = None
        except Exception as exc:
            self.status.last_error = str(exc)
            self.status.last_error_at = _now()
        self._write_status()

    def _write_status(self) -> None:
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self.status_path.write_text(
            json.dumps(asdict(self.status), indent=2, sort_keys=True),
            encoding="utf-8",
        )


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
