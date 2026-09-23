"""Count local storage once, without opening archives or following symlinks."""
from __future__ import annotations

import os
import stat
from pathlib import Path


def storage_usage(home: Path) -> dict[str, int]:
    totals = dict.fromkeys(("archive_bytes", "normalized_bytes", "raw_bytes",
                           "revisions_bytes", "cache_bytes", "database_bytes", "total_bytes"), 0)
    for directory, dirs, files in os.walk(home, followlinks=False):
        root = Path(directory)
        dirs[:] = [name for name in dirs if not (root / name).is_symlink()]
        parts = root.relative_to(home).parts
        for name in files:
            path = root / name
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    continue
                size = info.st_size
            except FileNotFoundError:
                continue
            totals["total_bytes"] += size
            if parts[:1] == ("archive",):
                totals["archive_bytes"] += size
                category = {"sources": "normalized_bytes", "raw": "raw_bytes",
                            "snapshots": "revisions_bytes"}.get(parts[1] if len(parts) > 1 else "")
                if category:
                    totals[category] += size
            elif parts[:1] == ("cache",):
                totals["cache_bytes"] += size
            elif parts == ("db",) and name.startswith("memory.sqlite"):
                totals["database_bytes"] += size
    return totals
