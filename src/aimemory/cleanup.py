from __future__ import annotations

import shutil
from pathlib import Path

from aimemory.config import AppPaths
from aimemory.state import folder_bytes, now, read_json, write_json


CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60


def run_auto_cleanup(paths: AppPaths, force: bool = False) -> dict:
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
