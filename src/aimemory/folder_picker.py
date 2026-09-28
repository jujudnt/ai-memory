"""System folder dialog (Finder on macOS, Explorer on Windows) for backup destinations."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from aimemory.cloud.providers import _windows_cloud_roots, provider_label
from aimemory.processes import background_creationflags

MAC_SCRIPT = """on run argv
  activate
  set chosen to choose folder with prompt (item 1 of argv) default location (POSIX file (item 2 of argv))
  return POSIX path of chosen
end run"""

WINDOWS_SCRIPT = """Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = $env:AIMEMORY_PROMPT
$dialog.ShowNewFolderButton = $true
$dialog.SelectedPath = $env:AIMEMORY_START
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($dialog.SelectedPath) }
$owner.Dispose()"""

_dialog_open = threading.Lock()


def picker_supported() -> bool:
    return sys.platform in {"darwin", "win32"}


def choose_folder(prompt: str, start: Path | None = None) -> Path | None:
    """Show the system folder dialog and wait for the user. None means cancelled."""
    if not picker_supported():
        raise ValueError("Le choix de dossier n'est disponible que sur macOS et Windows.")
    if not _dialog_open.acquire(blocking=False):
        raise ValueError("Une fenêtre de choix de dossier est déjà ouverte.")
    try:
        start = start if start and start.is_dir() else Path.home()
        if sys.platform == "darwin":
            result = subprocess.run(["osascript", "-e", MAC_SCRIPT, prompt, str(start)],
                                    capture_output=True, text=True, encoding="utf-8")
            if result.returncode and "-128" in result.stderr:
                return None
        else:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-Command", WINDOWS_SCRIPT],
                capture_output=True, text=True, encoding="utf-8", creationflags=background_creationflags(),
                env={**os.environ, "AIMEMORY_PROMPT": prompt, "AIMEMORY_START": str(start)},
            )
        if result.returncode:
            raise ValueError(f"Impossible d'ouvrir la fenêtre de choix de dossier : {result.stderr.strip()}")
        chosen = result.stdout.strip().lstrip("﻿")
        return Path(chosen) if chosen else None
    finally:
        _dialog_open.release()


def cloud_sync_roots(provider: str) -> list[Path]:
    """Local folders that mirror an online destination, when its client is installed.

    Google Drive is excluded: with the drive.file scope, AI Memory cannot see folders it
    did not create, so picking an existing one would silently create a duplicate.
    """
    if sys.platform == "win32":
        local = {"icloud-online": "icloud-drive", "dropbox-online": "dropbox", "onedrive-online": "onedrive"}.get(provider)
        return _windows_cloud_roots(local) if local else []
    if sys.platform != "darwin":
        return []
    home = Path.home()
    storage = home / "Library" / "CloudStorage"
    if provider == "icloud-online":
        candidates = [home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"]
    elif provider == "dropbox-online":
        candidates = [next((path for path in (storage / "Dropbox", home / "Dropbox") if path.is_dir()), storage / "Dropbox")]
    elif provider == "onedrive-online":
        candidates = sorted(storage.glob("OneDrive*")) if storage.is_dir() else []
    else:
        candidates = []
    return [path.resolve() for path in candidates if path.is_dir()]


def cloud_relative_folder(chosen: Path, roots: list[Path], provider: str) -> str:
    """Translate a folder picked in the local mirror into the destination's folder path."""
    chosen = chosen.expanduser().resolve()
    label = provider_label(provider)
    for root in roots:
        if chosen == root:
            raise ValueError(f"Choisissez ou créez un dossier dans {label}, pas sa racine.")
        if root in chosen.parents:
            return chosen.relative_to(root).as_posix()
    raise ValueError(f"Choisissez un dossier situé dans {label}.")
