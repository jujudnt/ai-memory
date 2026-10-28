"""Subprocess settings for commands run by the desktop app in the background."""
from __future__ import annotations

import sys
import os


def background_creationflags() -> int:
    # CREATE_NO_WINDOW: capturing stdout alone does not suppress a Windows console.
    return 0x08000000 if sys.platform == "win32" else 0


def lower_background_priority() -> None:
    """Yield to interactive apps without increasing an already lower priority."""
    if sys.platform == "win32":
        try:
            import psutil
            process = psutil.Process()
            if process.nice() not in {psutil.IDLE_PRIORITY_CLASS, psutil.BELOW_NORMAL_PRIORITY_CLASS}:
                process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        except (ImportError, OSError):
            pass
        except psutil.Error:
            pass
    elif hasattr(os, "setpriority"):
        try:
            priority = os.getpriority(os.PRIO_PROCESS, 0)
            os.setpriority(os.PRIO_PROCESS, 0, max(priority, 10))
        except OSError:
            pass
