from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from filelock import FileLock, Timeout

from aimemory.config import AppPaths
from aimemory.state import folder_bytes, now, read_json, write_json


CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60
COMPACT_MIN_BYTES = 64 * 1024 * 1024


def run_auto_cleanup(paths: AppPaths, force: bool = False) -> dict:
    try:
        with FileLock(str(paths.state / "cleanup.lock"), timeout=0):
            return _cleanup(paths, force)
    except Timeout:
        return read_json(paths.state / "cleanup-status.json")


def _cleanup(paths: AppPaths, force: bool) -> dict:
    status_path = paths.state / "cleanup-status.json"
    previous = read_json(status_path)
    if not force and _fresh(previous.get("last_run_at")):
        return previous

    targets = _cleanup_targets(paths.cache)
    freed = 0
    removed: list[str] = []
    for target in targets:
        if not target.exists():
            continue
        try:
            size = folder_bytes(target) if target.is_dir() else target.stat().st_size
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            freed += size
            removed.append(str(target))
        except OSError:
            continue
    status = {
        "last_run_at": now(),
        "freed_bytes": freed,
        "removed_count": len(removed),
        "removed": removed[-20:],
    }
    write_json(status_path, status)
    return status


def compact_search_index(paths: AppPaths, force: bool = False) -> dict:
    """Reclaim SQLite free pages while no import or cloud transfer owns the data."""
    status_path = paths.state / "index-maintenance.json"
    previous = read_json(status_path)
    if not force and previous.get("compact_index_version") == 1 and _fresh(previous.get("last_run_at")):
        return previous
    database = paths.db / "memory.sqlite"
    if not database.is_file():
        return {}
    try:
        with FileLock(str(paths.state / "sync.lock"), timeout=0), FileLock(str(paths.state / "operations.lock"), timeout=0):
            connection = sqlite3.connect(database, timeout=1)
            try:
                if connection.execute("SELECT 1 FROM sqlite_master WHERE name='compact_index_versions'").fetchone():
                    pending = connection.execute(
                        "SELECT 1 FROM conversations c LEFT JOIN compact_index_versions v "
                        "ON v.conversation_id=c.id WHERE COALESCE(v.version,0)<1 LIMIT 1"
                    ).fetchone()
                    if pending:
                        return {"status": "migrating"}
                    if not pending and previous.get("compact_index_version") != 1:
                        if shutil.disk_usage(paths.home).free < database.stat().st_size * 2 + 256 * 1024 * 1024:
                            return {"status": "waiting_for_space"}
                        connection.execute("INSERT INTO conversations_fts(conversations_fts) VALUES ('optimize')")
                        connection.commit()
                    else:
                        pending = True
                else:
                    pending = True
                page_size = connection.execute("PRAGMA page_size").fetchone()[0]
                pages = connection.execute("PRAGMA page_count").fetchone()[0]
                free = connection.execute("PRAGMA freelist_count").fetchone()[0]
                before = database.stat().st_size
                if free * page_size >= COMPACT_MIN_BYTES and free > pages / 4:
                    if shutil.disk_usage(paths.home).free < before * 2 + 256 * 1024 * 1024:
                        return {"status": "waiting_for_space", "reclaimable_bytes": free * page_size}
                    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        return {"status": "integrity_check_failed"}
                    connection.execute("VACUUM")
                result = {"last_run_at": now(), "freed_bytes": max(0, before - database.stat().st_size),
                          "compact_index_version": previous.get("compact_index_version", 0) if pending else 1}
                write_json(status_path, result)
                return result
            finally:
                connection.close()
    except (Timeout, sqlite3.OperationalError):
        return {"status": "busy"}


def _cleanup_targets(cache: Path) -> list[Path]:
    patterns = (
        "release-v*",
        "replaced-before-v*.app",
        "replaced-v*.app",
        "release-notes-v*.md",
        "desktop-v*-check.json",
        "drive-download-test",
        "*.zip",
    )
    targets: list[Path] = []
    if not cache.exists():
        return targets
    for pattern in patterns:
        targets.extend(path for path in cache.glob(pattern) if path.name != "exchange")
    return sorted(set(targets))


def _fresh(timestamp: str | None) -> bool:
    if not timestamp:
        return False
    from datetime import datetime

    try:
        then = datetime.fromisoformat(timestamp)
        current = datetime.fromisoformat(now())
    except ValueError:
        return False
    return (current - then).total_seconds() < CLEANUP_INTERVAL_SECONDS
