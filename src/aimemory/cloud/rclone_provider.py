from __future__ import annotations

import configparser
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from aimemory.cloud.google_drive import rclone_binary
from aimemory.cloud.providers import provider_label
from aimemory.state import write_json


@dataclass(frozen=True, slots=True)
class RcloneProviderSpec:
    provider: str
    remote_name: str
    backend: str

    @property
    def remote(self) -> str:
        return f"{self.remote_name}:AI-Memory"


RCLONE_PROVIDER_SPECS = {
    "dropbox-online": RcloneProviderSpec("dropbox-online", "aimemory-dropbox", "dropbox"),
    "onedrive-online": RcloneProviderSpec("onedrive-online", "aimemory-onedrive", "onedrive"),
    "icloud-online": RcloneProviderSpec("icloud-online", "aimemory-icloud", "iclouddrive"),
}


class RcloneCloudProvider:
    def __init__(self, paths, spec: RcloneProviderSpec):
        self.paths = paths
        self.spec = spec
        self.config = paths.home / "credentials" / "rclone.conf"

    @classmethod
    def for_provider(cls, paths, provider: str) -> "RcloneCloudProvider":
        if provider not in RCLONE_PROVIDER_SPECS:
            raise ValueError("Destination cloud inconnue.")
        return cls(paths, RCLONE_PROVIDER_SPECS[provider])

    def run(self, args: list[str], timeout: int = 600, config: Path | None = None, stdin=None) -> str:
        try:
            result = subprocess.run(
                [
                    rclone_binary(),
                    "--config",
                    str(config or self.config),
                    "--contimeout",
                    "15s",
                    "--timeout",
                    "60s",
                    "--retries",
                    "2",
                    *args,
                ],
                stdin=stdin if stdin is not None else subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{provider_label(self.spec.provider)} n'a pas répondu. Réessayez.") from None
        if result.returncode:
            if args[0] == "config":
                raise RuntimeError(f"Connexion {provider_label(self.spec.provider)} annulée ou refusée.")
            raise RuntimeError(_friendly_rclone_error(self.spec.provider, result.stderr, result.returncode))
        return result.stdout

    def connect(self, options: dict | None = None) -> None:
        options = options or {}
        self.config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config.parent.chmod(0o700)
        pending = self.config.with_suffix(".pending")
        if self.config.exists():
            shutil.copy2(self.config, pending)
        try:
            try:
                self.run(["config", "delete", self.spec.remote_name], timeout=30, config=pending)
            except RuntimeError:
                pass
            args = ["config", "create", self.spec.remote_name, self.spec.backend]
            if self.spec.provider in {"dropbox-online", "onedrive-online"}:
                args.extend(["config_is_local", "true"])
            if self.spec.provider == "onedrive-online":
                args.extend(["region", "global", "drive_type", options.get("onedrive_type") or "personal"])
            if self.spec.provider == "icloud-online":
                apple_id = str(options.get("apple_id") or "").strip()
                password = str(options.get("password") or "")
                if not apple_id or not password:
                    raise ValueError("Renseignez l'Apple ID et le mot de passe iCloud.")
                obscured = self.run(["obscure", password], timeout=30, config=pending).strip()
                args.extend(["apple_id", apple_id, "password", obscured])
            args.append("--no-output")
            self.run(args, timeout=300, config=pending)
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(pending)
            if not parser.has_section(self.spec.remote_name):
                raise RuntimeError(f"Connexion {provider_label(self.spec.provider)} incomplète.")
            if self.spec.provider != "icloud-online" and not parser.has_option(self.spec.remote_name, "token"):
                raise RuntimeError(f"{provider_label(self.spec.provider)} n'a pas renvoyé de jeton d'accès.")
            pending.chmod(0o600)
            self.run(["mkdir", self.spec.remote], config=pending)
            os.replace(pending, self.config)
            write_json(
                self.paths.state / "cloud.json",
                {
                    "provider": self.spec.provider,
                    "root": "AI-Memory",
                    "remote": self.spec.remote_name,
                    "mode": "online",
                },
            )
            write_json(self.paths.state / "sync-status.json", {"status": "connected"})
        finally:
            pending.unlink(missing_ok=True)

    def exchange(self, local: Path, progress) -> None:
        for source, destination, phase in ((str(local), self.spec.remote, "uploading"), (self.spec.remote, str(local), "downloading")):
            progress(phase, bytes=0, totalBytes=0, speed=0)
            args = [
                rclone_binary(),
                "--config",
                str(self.config),
                "copy",
                source,
                destination,
                "--ignore-existing",
                "--exclude",
                ".tmp-*",
                "--use-json-log",
                "--stats",
                "2s",
                "--stats-log-level",
                "NOTICE",
                "--contimeout",
                "15s",
                "--timeout",
                "60s",
                "--retries",
                "2",
            ]
            process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            started = time.monotonic()
            try:
                while True:
                    try:
                        _, output = process.communicate(timeout=2)
                        done = True
                    except subprocess.TimeoutExpired as exc:
                        output = exc.stderr or b""
                        done = False
                    stats = {}
                    for line in output.splitlines()[-12:]:
                        try:
                            record = json.loads(line)
                            if "stats" in record:
                                stats = record["stats"]
                        except (ValueError, UnicodeDecodeError):
                            pass
                    progress(phase, **{key: stats.get(key, 0) for key in ("bytes", "totalBytes", "speed", "transfers", "totalTransfers")})
                    if done:
                        break
                    if time.monotonic() - started > 3600:
                        raise TimeoutError(f"Transfert {provider_label(self.spec.provider)} trop long. Relancez la synchronisation.")
                if process.returncode:
                    raise RuntimeError(_friendly_rclone_error(self.spec.provider, output, process.returncode))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def disconnect(self) -> None:
        try:
            self.run(["config", "delete", self.spec.remote_name], timeout=30)
        except RuntimeError:
            pass
        (self.paths.state / "cloud.json").unlink(missing_ok=True)


def _friendly_rclone_error(provider: str, output: str | bytes | None, code: int) -> str:
    text = (output or "").decode("utf-8", "ignore") if isinstance(output, bytes) else (output or "")
    lowered = text.lower()
    name = provider_label(provider)
    if any(marker in lowered for marker in ("insufficient storage", "not enough space", "quota", "storage full", "drive is full")):
        return f"{name} est plein. Libérez de l'espace ou changez de destination cloud."
    if any(marker in lowered for marker in ("unauthorized", "invalid_grant", "access denied", "forbidden", "authentication", "auth")):
        return f"Connexion {name} refusée ou expirée. Reconnectez ce compte."
    if provider == "icloud-online" and any(marker in lowered for marker in ("2fa", "two-factor", "verification", "mfa")):
        return "iCloud demande une vérification à deux facteurs. Validez la connexion Apple puis réessayez."
    return f"{name} a échoué (code {code}). Vérifiez la connexion puis relancez."
