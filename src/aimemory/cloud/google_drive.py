from __future__ import annotations

import configparser
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from aimemory.state import write_json


def rclone_binary() -> str:
    name = "rclone.exe" if os.name == "nt" else "rclone"
    candidates = [Path(sys.executable).with_name(name),
                  Path(getattr(sys, "_MEIPASS", ".")) / name,
                  Path(__file__).resolve().parents[3] / "vendor" / name]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    installed = shutil.which(name)
    if installed:
        return installed
    raise RuntimeError("Cloud component missing. Install the complete AI Memory release.")


class GoogleDriveProvider:
    name = "google-drive"
    remote = "aimemory:AI-Memory"

    def __init__(self, paths):
        self.paths = paths
        self.config = paths.home / "credentials" / "rclone.conf"

    def run(self, args: list[str], timeout: int = 600, config: Path | None = None) -> str:
        try:
            result = subprocess.run(
                [rclone_binary(), "--config", str(config or self.config),
                 "--contimeout", "15s", "--timeout", "60s", "--retries", "2", *args],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("Google operation timed out. Please try again.") from None
        if result.returncode:
            # OAuth/config output can contain credentials; never return it to the UI or logs.
            if args[0] == "config":
                raise RuntimeError("Google authorization failed or was cancelled. Please reconnect.")
            raise RuntimeError(_friendly_google_error(result.stderr, result.returncode))
        return result.stdout

    def connect(self, oauth_client: dict | None = None) -> None:
        self.config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config.parent.chmod(0o700)
        pending = self.config.with_suffix(".pending")
        try:
            client_args = []
            if oauth_client:
                client = oauth_client.get("installed", {})
                if not client.get("client_id") or not client.get("client_secret"):
                    raise ValueError("Choose a Google OAuth JSON file for a Desktop application.")
                client_args = ["client_id", client["client_id"], "client_secret", client["client_secret"]]
            # drive.file limits access to files created through this OAuth application.
            self.run(["config", "create", "aimemory", "drive", "scope", "drive.file",
                      "config_is_local", "true", "config_change_team_drive", "false", *client_args, "--no-output"],
                     timeout=300, config=pending)
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(pending)
            if not parser.has_option("aimemory", "token"):
                raise RuntimeError("Google did not return an authorization token.")
            token = json.loads(parser.get("aimemory", "token"))
            if not token.get("access_token"):
                raise RuntimeError("Google authorization is incomplete.")
            pending.chmod(0o600)
            self.run(["mkdir", self.remote], config=pending)
            os.replace(pending, self.config)
            write_json(self.paths.state / "cloud.json", {"provider": self.name, "root": "AI-Memory", "oauth_client": "custom" if client_args else "shared"})
        finally:
            pending.unlink(missing_ok=True)

    def exchange(self, local: Path, progress) -> None:
        for source, destination, phase in ((str(local), self.remote, "uploading"), (self.remote, str(local), "downloading")):
            progress(phase, bytes=0, totalBytes=0, speed=0)
            args = [rclone_binary(), "--config", str(self.config), "copy", source, destination,
                    "--ignore-existing", "--exclude", ".tmp-*", "--use-json-log",
                    "--stats", "2s", "--stats-log-level", "NOTICE", "--contimeout", "15s",
                    "--timeout", "60s", "--retries", "2"]
            process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
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
                        raise TimeoutError("Google Drive transfer timed out. Completed files are preserved; retry sync.")
                if process.returncode:
                    raise RuntimeError(_friendly_google_error(output, process.returncode))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def disconnect(self) -> None:
        self.config.unlink(missing_ok=True)
        (self.paths.state / "cloud.json").unlink(missing_ok=True)


def _friendly_google_error(output: str | bytes | None, code: int) -> str:
    text = (output or "").decode("utf-8", "ignore") if isinstance(output, bytes) else (output or "")
    lowered = text.lower()
    full_markers = (
        "storagequotaexceeded",
        "insufficient storage",
        "not enough space",
        "storage quota",
        "quota bytes",
        "drive storage",
        "cannotuploadfile",
    )
    if any(marker in lowered for marker in full_markers):
        return (
            "Google Drive est plein. Libérez de l'espace, changez de destination cloud, "
            "ou déconnectez Google Drive puis choisissez iCloud/OneDrive/Dropbox."
        )
    if "rate limit" in lowered or "user rate limit exceeded" in lowered:
        return "Google Drive limite temporairement les transferts. Réessayez dans quelques minutes."
    return f"Google Drive operation failed (code {code}). Check your connection and reconnect if access was revoked."
