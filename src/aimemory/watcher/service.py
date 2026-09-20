from __future__ import annotations

import time
import os
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from aimemory.service import MemoryService
from aimemory.state import read_json, write_json
from filelock import FileLock, Timeout


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
    pid: int = 0


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
        lock = FileLock(str(self.service.paths.state / "watcher.lock"), timeout=0)
        try:
            lock.acquire()
        except Timeout:
            return
        self.status.running = True
        self.status.pid = os.getpid()
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
            lock.release()

    def scan_once(self, codex_home: Path | None = None) -> None:
        self.status.last_scan_at = _now()
        self.status.total_scans += 1
        try:
            result = self.service.import_all(codex_home=codex_home)
            self.status.scanned = result.scanned
            self.status.imported = result.imported
            self.status.skipped = result.skipped
            if result.errors:
                raise RuntimeError(f"{len(result.errors)} import failures: {result.errors[0]['error']}")
            self.status.last_success_at = _now()
            self.status.last_error = None
            self.status.last_error_at = None
        except Timeout:
            return  # A manual import, audit or sync currently owns the shared data.
        except Exception as exc:
            self.status.last_error = str(exc)
            self.status.last_error_at = _now()
        self._write_status()
        # Cloud failures do not erase the collector's independent health signal.
        thread = getattr(self, "_sync_thread", None)
        if read_json(self.service.paths.state / "sync-status.json").get("requires_action"):
            return
        if (thread is None or not thread.is_alive()) and time.monotonic() - getattr(self, "_last_sync", -60) >= 60:
            self._last_sync = time.monotonic()
            def synchronize():
                try:
                    self.service.sync_now()
                except Exception:
                    pass  # CloudSync persists failures and the next minute retries.
            self._sync_thread = threading.Thread(target=synchronize, daemon=True)
            self._sync_thread.start()

    def _write_status(self) -> None:
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.status_path, asdict(self.status))


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
