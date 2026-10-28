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
from aimemory import __version__
from aimemory.cleanup import run_auto_cleanup, compact_search_index
from aimemory.processes import lower_background_priority
from aimemory.watcher.events import SourceEvents


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
    version: str = __version__
    collection_mode: str = "polling"


class WatcherService:
    """Event-driven collector with periodic reconciliation and polling fallback.

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
        if not once:
            lower_background_priority()
        events = SourceEvents(self.service.paths.state, codex_home)
        native = not once and events.start()
        self.status.collection_mode = "events" if native else "polling"
        self.status.pid = os.getpid()
        self.status.started_at = _now()
        self._write_status()
        try:
            last_scan = float("-inf")
            idle_interval = max(interval_seconds, 60.0)
            while True:
                current = time.monotonic()
                due = current - last_scan >= (idle_interval if native else interval_seconds)
                if due or (native and events.changed.is_set() and current - last_scan >= interval_seconds):
                    events.changed.clear()
                    self.scan_once(codex_home)
                    last_scan = time.monotonic()
                    if native:
                        try:
                            events.refresh()
                            native = events.alive
                        except (OSError, RuntimeError):
                            native = False
                        if not native:
                            events.close()
                            self.status.collection_mode = "polling"
                else:
                    self._maybe_sync()
                if once:
                    break
                if native:
                    # Requests wake immediately; source bursts coalesce at the normal interval.
                    events.requested.wait(timeout=interval_seconds)
                    events.requested.clear()
                else:
                    time.sleep(interval_seconds)
        finally:
            events.close()
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
        # Index maintenance is local work, including when cloud sync is paused,
        # offline, or waiting to retry. Its own locks defer it during transfers.
        compact_search_index(self.service.paths)
        self._maybe_sync()

    def _maybe_sync(self) -> None:
        # Cloud failures do not erase the collector's independent health signal.
        thread = getattr(self, "_sync_thread", None)
        sync_status = read_json(self.service.paths.state / "sync-status.json")
        request = self.service.paths.state / "sync-request.json"
        requested = request.exists()
        requires_action = sync_status.get("requires_action")
        # Older versions permanently blocked even generic iCloud 401/403 errors.
        # Recheck them once with the saved session and the precise classifier.
        if (requires_action and sync_status.get("error_category") == "auth"
                and "auth_action_confirmed" not in sync_status
                and read_json(self.service.paths.state / "cloud.json").get("provider") == "icloud-online"
                and not read_json(self.service.paths.state / "icloud-auth.json")):
            requires_action = False
        if not requested and (requires_action or
                (sync_status.get("retry_after") or 0) > time.time() or
                read_json(self.service.paths.state / "sync-preferences.json").get("paused")):
            return
        sync_interval = 15 if sync_status.get("status") == "waiting_local_cloud" else 60
        if (thread is None or not thread.is_alive()) and (requested or time.monotonic() - getattr(self, "_last_sync", -60) >= sync_interval):
            self._last_sync = time.monotonic()
            def synchronize():
                try:
                    if requested:
                        request.unlink(missing_ok=True)
                        write_json(self.service.paths.state / "sync-preferences.json", {"paused": False})
                    self.service.sync_now()
                    run_auto_cleanup(self.service.paths)
                    compact_search_index(self.service.paths)
                except Exception:
                    pass  # CloudSync persists failures and the next minute retries.
                finally:
                    # Rest after completion too: a long sync must not cause an
                    # immediate full cloud listing on the next collector tick.
                    self._last_sync = time.monotonic()
            self._sync_thread = threading.Thread(target=synchronize, daemon=True)
            self._sync_thread.start()

    def _write_status(self) -> None:
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.status_path, asdict(self.status))


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
