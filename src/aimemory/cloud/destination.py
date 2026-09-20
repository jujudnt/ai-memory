from __future__ import annotations

from aimemory.cloud.providers import RCLONE_DIRECT_PROVIDERS


def cloud_folder(value: str) -> str:
    value = value.strip().strip("/")
    if not value or any(ord(c) < 32 for c in value) or ":" in value or "\\" in value:
        raise ValueError("Indiquez un dossier cloud, par exemple Sauvegardes/AI-Memory.")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("Le chemin du dossier cloud est invalide.")
    return value


def remote_path(config: dict) -> str:
    from aimemory.cloud.rclone_provider import RCLONE_PROVIDER_SPECS

    provider = config["provider"]
    if provider == "google-drive":
        name = "aimemory"
    elif provider in RCLONE_DIRECT_PROVIDERS:
        name = RCLONE_PROVIDER_SPECS[provider].remote_name
    else:
        raise ValueError("Ce compte n'est pas une destination cloud en ligne.")
    return f"{name}:{cloud_folder(config.get('root') or 'AI-Memory')}"
