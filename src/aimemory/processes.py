"""Subprocess settings for commands run by the desktop app in the background."""
from __future__ import annotations

import sys


def background_creationflags() -> int:
    # CREATE_NO_WINDOW: capturing stdout alone does not suppress a Windows console.
    return 0x08000000 if sys.platform == "win32" else 0
