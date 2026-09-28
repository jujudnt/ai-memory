from __future__ import annotations

from pathlib import Path
import json
import os
import sys


LOCAL_FOLDER_PROVIDERS = {"local-folder", "icloud-drive", "onedrive", "dropbox"}
RCLONE_DIRECT_PROVIDERS = {"dropbox-online", "onedrive-online", "icloud-online"}

PROVIDER_LABELS = {
    "google-drive": "Google Drive",
    "icloud-online": "iCloud Drive",
    "dropbox-online": "Dropbox",
    "onedrive-online": "OneDrive",
    "icloud-drive": "iCloud Drive du Mac",
    "onedrive": "OneDrive du Mac",
    "dropbox": "Dropbox du Mac",
    "local-folder": "Dossier synchronisé",
}


def provider_label(provider: str | None) -> str:
    label = PROVIDER_LABELS.get(provider or "", "Sauvegarde cloud")
    return label.replace("du Mac", "du PC") if sys.platform == "win32" else label


def resolve_folder_root(provider: str, folder: str = "") -> Path:
    if provider == "local-folder":
        if not folder.strip():
            raise ValueError("Choisissez un dossier de synchronisation.")
        return Path(folder).expanduser().resolve()
    if sys.platform == "win32" and provider in {"icloud-drive", "onedrive", "dropbox"}:
        roots = _windows_cloud_roots(provider)
        if len(roots) > 1:
            raise ValueError("Plusieurs dossiers cloud sont disponibles. Choisissez le compte voulu via Dossier synchronisé.")
        if not roots:
            raise ValueError(f"{provider_label(provider)} est introuvable. Connectez le client cloud ou choisissez son dossier via Dossier synchronisé.")
        return roots[0] / "AI-Memory"
    if provider == "icloud-drive":
        root = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
        if not root.is_dir():
            raise ValueError("iCloud Drive est introuvable sur ce Mac. Activez iCloud Drive puis réessayez.")
        return (root / "AI-Memory").resolve()
    if provider == "onedrive":
        cloud = Path.home() / "Library" / "CloudStorage"
        matches = sorted(path for path in cloud.glob("OneDrive*") if path.is_dir())
        fallback = Path.home() / "OneDrive"
        base = matches[0] if matches else fallback if fallback.is_dir() else None
        if base is None:
            raise ValueError("OneDrive est introuvable sur ce Mac. Installez/connectez OneDrive puis réessayez.")
        root = base / "AI-Memory"
        return root.resolve()
    if provider == "dropbox":
        cloud = Path.home() / "Library" / "CloudStorage" / "Dropbox"
        fallback = Path.home() / "Dropbox"
        base = cloud if cloud.is_dir() else fallback if fallback.is_dir() else None
        if base is None:
            raise ValueError("Dropbox est introuvable sur ce Mac. Installez/connectez Dropbox puis réessayez.")
        root = base / "AI-Memory"
        return root.resolve()
    raise ValueError("Destination cloud inconnue.")


def _windows_cloud_roots(provider: str) -> list[Path]:
    home = Path.home()
    candidates: list[Path] = []
    if provider == "icloud-drive":
        candidates = [home / "iCloudDrive", home / "iCloud Drive"]
    elif provider == "onedrive":
        candidates = [Path(os.environ[key]) for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial") if os.environ.get(key)]
        candidates.extend(home.glob("OneDrive*"))
    elif provider == "dropbox":
        # Dropbox documents info.json for moved and business-account folders.
        for key in ("LOCALAPPDATA", "APPDATA"):
            if not os.environ.get(key):
                continue
            try:
                data = json.loads((Path(os.environ[key]) / "Dropbox/info.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                candidates.extend(Path(account["path"]) for account in data.values()
                                  if isinstance(account, dict) and isinstance(account.get("path"), str) and account["path"])
        candidates.append(home / "Dropbox")
    return sorted({path.resolve() for path in candidates if path.is_dir()})


def validate_sync_root(root: Path, home: Path) -> None:
    resolved_home = home.resolve()
    resolved_root = root.expanduser().resolve()
    if (
        resolved_root == resolved_home
        or resolved_home in resolved_root.parents
        or resolved_root in resolved_home.parents
    ):
        raise ValueError("Choisissez un dossier séparé des données AI Memory.")
