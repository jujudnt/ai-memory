from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from filelock import FileLock

from aimemory.cloud.google_drive import GoogleDriveProvider
from aimemory.cloud.providers import LOCAL_FOLDER_PROVIDERS, RCLONE_DIRECT_PROVIDERS, validate_sync_root
from aimemory.cloud.rclone_provider import RcloneCloudProvider
from aimemory.archive.raw_backup import RAW_PATTERN, read_raw
from aimemory.state import atomic_write, now, read_json, write_json


class CloudSync:
    def __init__(self, service):
        self.service = service
        self.paths = service.paths
        self.status_path = self.paths.state / "sync-status.json"

    def run(self) -> dict:
        with FileLock(str(self.paths.state / "sync.lock"), timeout=1):
            clear_cancel(self.paths)
            config = read_json(self.paths.state / "cloud.json")
            if not config:
                return {"status": "not-configured"}
            status = read_json(self.status_path)
            status.update(status="preparing", started_at=now(), error=None)
            def progress(phase, **details):
                check_cancelled(self.paths)
                status.update(status=phase, heartbeat_at=now(), **details)
                write_json(self.status_path, status)
            try:
                progress("preparing")
                # Only immutable objects cross devices, never SQLite or credentials.
                exchange = self.paths.cache / "exchange"
                exchange.mkdir(parents=True, exist_ok=True)
                for category in ("snapshots", "raw"):
                    for path in (self.paths.archive / category).rglob("*"):
                        check_cancelled(self.paths)
                        if path.is_file() and not path.name.startswith("."):
                            target = exchange / path.relative_to(self.paths.archive)
                            if not target.exists():
                                atomic_write(target, path.read_bytes())
                if config["provider"] == "google-drive":
                    GoogleDriveProvider(self.paths).exchange(exchange, progress)
                elif config["provider"] in RCLONE_DIRECT_PROVIDERS:
                    RcloneCloudProvider.for_provider(self.paths, config["provider"]).exchange(exchange, progress)
                elif config["provider"] in LOCAL_FOLDER_PROVIDERS:
                    remote = Path(config["root"]).expanduser().resolve()
                    validate_sync_root(remote, self.paths.home)
                    for source, destination, phase in ((exchange, remote, "uploading"), (remote, exchange, "downloading")):
                        progress(phase)
                        _ensure_available_space(source, destination)
                        for path in source.rglob("*"):
                            check_cancelled(self.paths)
                            if path.is_file() and not path.is_symlink() and not path.name.startswith("."):
                                target = destination / path.relative_to(source)
                                if not target.exists():
                                    atomic_write(target, path.read_bytes())
                else:
                    raise ValueError("Unsupported storage provider")
                progress("verifying")
                known = read_json(self.paths.state / "synced-objects.json")
                indexed = 0
                for path in sorted(exchange.rglob("*")):
                    check_cancelled(self.paths)
                    if not path.is_file():
                        continue
                    relative = path.relative_to(exchange)
                    if path.is_symlink() or not re.fullmatch(r"(?:snapshots/[a-zA-Z0-9_-]+/[a-f0-9]{64}\.json\.(?:gz|zst)|" + RAW_PATTERN + ")", relative.as_posix()):
                        raise ValueError("Unexpected object in cloud archive")
                    if relative.as_posix() in known:
                        continue
                    payload = path.read_bytes()
                    if relative.parts[0] == "snapshots":
                        if hashlib.sha256(payload).hexdigest() != path.name.split(".")[0]:
                            path.unlink()
                            raise ValueError("Cloud archive checksum mismatch. Retry synchronization.")
                        conversation = self.service.archive.read(path)
                        if conversation.id != relative.parts[1]:
                            raise ValueError("Cloud archive identity mismatch")
                        # Skip already present local snapshots; remote revisions are indexed.
                        legacy_codex = conversation.source == "codex" and conversation.metadata.get("parser_version", 0) < 2
                        if not legacy_codex and not (self.paths.archive / relative).exists():
                            with self.service.lock.acquire(timeout=60):
                                indexed += int(self.service.accept_conversation(conversation))
                    else:
                        try:
                            read_raw(exchange, relative.as_posix())
                        except ValueError:
                            path.unlink()
                            raise ValueError("Raw cloud backup checksum mismatch. Retry synchronization.")
                    target = self.paths.archive / relative
                    if not target.exists():
                        atomic_write(target, payload)
                    known[relative.as_posix()] = True
                    if len(known) % 10 == 0:
                        progress("verifying", object_count=len(known))
                write_json(self.paths.state / "synced-objects.json", known)
                status.update(status="synced", last_success_at=now(), indexed=indexed,
                              object_count=len(known), error=None)
            except Exception as exc:
                status.update(status="error", error=str(exc), failed_at=now())
                raise
            finally:
                write_json(self.status_path, status)
            return status


def cancel_active_sync(paths) -> None:
    write_json(paths.state / "sync-cancel.json", {"cancelled_at": now()})
    _terminate_rclone(paths)


def check_cancelled(paths) -> None:
    if (paths.state / "sync-cancel.json").exists():
        raise RuntimeError("Synchronisation annulée pour changer de destination.")


def clear_cancel(paths) -> None:
    (paths.state / "sync-cancel.json").unlink(missing_ok=True)


def _terminate_rclone(paths) -> None:
    config = str(paths.home / "credentials" / "rclone.conf")
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/IM", "rclone.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    subprocess.run(
        ["pkill", "-f", f"rclone.*{config}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _ensure_available_space(source: Path, destination: Path) -> None:
    missing = 0
    for path in source.rglob("*"):
        if path.is_file() and not path.is_symlink() and not path.name.startswith("."):
            target = destination / path.relative_to(source)
            if not target.exists():
                missing += path.stat().st_size
    if not missing:
        return
    probe = destination
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    reserve = max(100 * 1024 * 1024, missing // 20)
    if free < missing + reserve:
        raise RuntimeError(
            "Espace insuffisant sur la destination cloud locale. "
            "Libérez de l'espace ou choisissez une autre destination."
        )
