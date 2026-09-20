from __future__ import annotations

import hashlib
import errno
import gzip
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from filelock import FileLock

from aimemory.cloud.google_drive import rclone_binary
from aimemory.cloud.providers import LOCAL_FOLDER_PROVIDERS, RCLONE_DIRECT_PROVIDERS, validate_sync_root
from aimemory.cloud.rclone_provider import RcloneCloudProvider
from aimemory.cloud.destination import remote_path
from aimemory.archive.raw_backup import RAW_PATTERN, read_raw
from aimemory.state import atomic_write, now, read_json, write_json

OBJECT_PATTERN = re.compile(
    r"(?:snapshots/[a-zA-Z0-9_-]+/[a-f0-9]{64}\.json\.(?:gz|zst)|" + RAW_PATTERN + ")"
)


class CloudFolderPending(RuntimeError):
    """The local cloud client has not made an item accessible yet."""


class CloudSync:
    def __init__(self, service):
        self.service = service
        self.paths = service.paths
        self.status_path = self.paths.state / "sync-status.json"

    def run(self, pull_only: bool = False) -> dict:
        with FileLock(str(self.paths.state / "sync.lock"), timeout=1):
            clear_cancel(self.paths)
            config = read_json(self.paths.state / "cloud.json")
            if not config:
                return {"status": "not-configured"}
            status = read_json(self.status_path)
            status.update(status="preparing", started_at=now(), error=None, requires_action=False,
                          transfers=0, totalTransfers=0, bytes=0, totalBytes=0, speed=0)
            def progress(phase, **details):
                check_cancelled(self.paths)
                status.update(status=phase, heartbeat_at=now(), **details)
                if "error" not in details:
                    status["error"] = None
                write_json(self.status_path, status)
            try:
                progress("preparing")
                remote = _remote_archive(self.paths, config)
                known = read_json(self.paths.state / "synced-objects.json")
                remote_identity = _remote_identity(config)
                previous_identity = known.pop("__remote_identity__", None)
                destination_changed = bool(previous_identity and previous_identity != remote_identity)
                if destination_changed:
                    known = {}
                remote_objects = remote.list(progress)
                known = {relative: True for relative in known if relative in remote_objects}
                local_objects = list(
                    _iter_local_objects(
                        self.paths.archive,
                        include_current_sources=True,
                    )
                )
                uploads = [] if pull_only else [
                    (relative, path) for relative, path in local_objects if relative not in remote_objects
                ]
                if uploads and config["provider"] in LOCAL_FOLDER_PROVIDERS:
                    _ensure_available_space_for_files((path for _, path in uploads), remote.root)
                for index, (relative, path) in enumerate(uploads, start=1):
                    check_cancelled(self.paths)
                    progress("uploading", object_count=len(remote_objects), transfers=index - 1, totalTransfers=len(uploads))
                    remote.upload(path, relative)
                    remote_objects.add(relative)
                    known[relative] = True
                    progress("uploading", object_count=len(remote_objects), transfers=index,
                             totalTransfers=len(uploads))
                if not uploads:
                    progress("uploading", object_count=len(remote_objects))
                progress("verifying")
                indexed = 0
                incoming = self.paths.cache / "incoming"
                shutil.rmtree(incoming, ignore_errors=True)
                downloads = sorted(path for path in remote_objects if path not in known)
                for index, relative_string in enumerate(downloads, start=1):
                    check_cancelled(self.paths)
                    relative = Path(relative_string)
                    if not _allowed_object(relative_string):
                        raise ValueError("Unexpected object in cloud archive")
                    progress("downloading", object_count=len(known), transfers=index - 1, totalTransfers=len(downloads))
                    path = incoming / relative
                    remote.download(relative_string, path)
                    payload = path.read_bytes()
                    if relative.parts[0] == "snapshots":
                        if hashlib.sha256(payload).hexdigest() != path.name.split(".")[0]:
                            path.unlink()
                            _wait_for_incomplete_icloud_item(remote, relative_string)
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
                            _hydrate_raw_dependencies(
                                incoming,
                                relative_string,
                                remote,
                                remote_objects,
                            )
                            read_raw(incoming, relative.as_posix())
                        except CloudFolderPending:
                            raise
                        except (OSError, ValueError, KeyError, json.JSONDecodeError):
                            path.unlink()
                            _wait_for_incomplete_icloud_item(remote, relative_string)
                            raise ValueError("Raw cloud backup checksum mismatch. Retry synchronization.")
                    target = self.paths.archive / relative
                    if not target.exists():
                        atomic_write(target, payload)
                    known[relative_string] = True
                    progress("downloading", object_count=len(known), transfers=index, totalTransfers=len(downloads))
                    if len(known) % 10 == 0:
                        progress("verifying", object_count=len(known))
                for relative_string, _ in local_objects:
                    if relative_string in remote_objects:
                        known[relative_string] = True
                known["__remote_identity__"] = remote_identity
                write_json(self.paths.state / "synced-objects.json", known)
                retention = prune_synced_local_copies(self.paths, known)
                status.update(status="synced", last_success_at=now(), indexed=indexed,
                              object_count=len(remote_objects), retention=retention, error=None)
            except CloudFolderPending as exc:
                status.update(status="waiting_local_cloud", error=str(exc), heartbeat_at=now(),
                              requires_action=False, message=str(exc))
            except Exception as exc:
                status.update(status="error", error=str(exc), failed_at=now())
                # A background retry must not repeatedly prompt the user's Apple devices.
                if config.get("provider") == "icloud-online":
                    status["requires_action"] = True
                raise
            finally:
                shutil.rmtree(self.paths.cache / "incoming", ignore_errors=True)
                shutil.rmtree(self.paths.cache / "exchange", ignore_errors=True)
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


def _ensure_available_space_for_files(paths, destination: Path) -> None:
    missing = sum(path.stat().st_size for path in paths if path.exists())
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


def prune_synced_local_copies(paths, known: dict) -> dict:
    removable_roots = ("raw/", "snapshots/")
    freed = 0
    removed: list[str] = []
    for relative in sorted(path for path in known if path.startswith(removable_roots) and _allowed_object(path)):
        target = paths.archive / relative
        if not target.is_file():
            continue
        try:
            size = target.stat().st_size
            target.unlink()
            freed += size
            removed.append(relative)
            _prune_empty_parents(target.parent, paths.archive)
        except OSError:
            continue
    status = {
        "last_run_at": now(),
        "freed_bytes": freed,
        "removed_count": len(removed),
        "removed_raw_count": sum(1 for path in removed if path.startswith("raw/")),
        "removed_revision_count": sum(1 for path in removed if path.startswith("snapshots/")),
        "removed": removed[-20:],
    }
    write_json(paths.state / "retention-status.json", status)
    return status


def _prune_empty_parents(path: Path, stop: Path) -> None:
    while path != stop and path.exists():
        try:
            path.rmdir()
        except OSError:
            return
        path = path.parent


def _iter_local_objects(archive: Path, include_current_sources: bool = False):
    seen: set[str] = set()
    # Send one immutable current revision per conversation before its history.
    if include_current_sources:
        for path in sorted((archive / "sources").rglob("*.json.*")):
            if not path.is_file() or path.is_symlink() or path.name.startswith("."):
                continue
            suffix = ".json.zst" if path.name.endswith(".json.zst") else ".json.gz"
            conversation_id = path.name.removesuffix(suffix)
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", conversation_id):
                raise ValueError("Unexpected current conversation archive")
            payload = path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            relative = f"snapshots/{conversation_id}/{digest}{suffix}"
            target = archive / relative
            if not target.exists():
                atomic_write(target, payload)
            seen.add(relative)
            yield relative, target
    for category in ("snapshots", "raw"):
        for path in sorted((archive / category).rglob("*")):
            if path.is_file() and not path.is_symlink() and not path.name.startswith("."):
                relative = path.relative_to(archive).as_posix()
                if not _allowed_object(relative):
                    raise ValueError("Unexpected object in local archive")
                if relative not in seen:
                    seen.add(relative)
                    yield relative, path


def _hydrate_raw_dependencies(
    incoming: Path,
    relative: str,
    remote,
    remote_objects: set[str],
) -> None:
    seen: set[str] = set()
    current = relative
    while current.endswith(".delta.json.gz"):
        if current in seen or not re.fullmatch(RAW_PATTERN, current):
            raise ValueError("Invalid raw backup chain")
        seen.add(current)
        path = incoming / current
        if not path.exists():
            if current not in remote_objects:
                _wait_for_incomplete_icloud_item(remote, current)
                raise ValueError("Raw backup dependency missing from cloud")
            remote.download(current, path)
        payload = json.loads(gzip.decompress(path.read_bytes()))
        parent = Path(payload["parent"]).as_posix()
        if not re.fullmatch(RAW_PATTERN, parent) or parent not in remote_objects:
            if re.fullmatch(RAW_PATTERN, parent):
                _wait_for_incomplete_icloud_item(remote, parent)
            raise ValueError("Raw backup dependency missing from cloud")
        parent_path = incoming / parent
        if not parent_path.exists():
            remote.download(parent, parent_path)
        current = parent


def _wait_for_incomplete_icloud_item(remote, relative: str) -> None:
    """Turn an iCloud placeholder/partial listing into a retryable cloud wait.

    iCloud Drive can expose a child delta before it exposes its parent, or expose
    an item before its bytes have finished downloading.  Those states have no
    useful checksum yet, so they must never be reported as archive corruption.
    """
    wait_for_item = getattr(remote, "wait_for_item", None)
    if wait_for_item:
        message = wait_for_item(relative)
        if message:
            raise CloudFolderPending(message)


def _allowed_object(relative: str) -> bool:
    return bool(OBJECT_PATTERN.fullmatch(relative))


def _remote_identity(config: dict) -> str:
    if config.get("connection_id"):
        return str(config["connection_id"])
    stable = {key: config.get(key) for key in ("provider", "root", "remote", "oauth_client", "mode")}
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


def _remote_archive(paths, config: dict):
    provider = config["provider"]
    if provider == "google-drive":
        return RcloneArchiveRemote(paths, "google-drive", remote_path(config))
    if provider in RCLONE_DIRECT_PROVIDERS:
        return RcloneArchiveRemote(paths, provider, remote_path(config))
    if provider in LOCAL_FOLDER_PROVIDERS:
        root = Path(config["root"]).expanduser().resolve()
        validate_sync_root(root, paths.home)
        return LocalArchiveRemote(root, icloud=provider == "icloud-drive")
    raise ValueError("Unsupported storage provider")


class LocalArchiveRemote:
    def __init__(self, root: Path, icloud: bool = False):
        self.root = root
        self.icloud = icloud
        self.progress = lambda *args, **kwargs: None

    def _io(self, operation, path: Path):
        for attempt in range(3):
            try:
                return operation()
            except OSError as exc:
                if exc.errno not in {errno.EDEADLK, errno.EAGAIN, errno.EBUSY}:
                    raise
                label = "iCloud Drive" if self.icloud else "Le dossier cloud"
                message = (
                    f"{label} n'a pas encore rendu accessible {path.name}. "
                    "Nouvelle tentative automatique avec le watcher. "
                    "Si l'attente persiste, ouvrez le dossier AI-Memory dans le Finder "
                    "et choisissez Télécharger maintenant."
                )
                self.progress("waiting_local_cloud", error=message)
                if self.icloud:
                    _request_icloud_download(path)
                if attempt == 2:
                    raise CloudFolderPending(message) from exc
                time.sleep(attempt + 1)

    def list(self, progress) -> set[str]:
        self.progress = progress
        self._io(lambda: self.root.mkdir(parents=True, exist_ok=True), self.root)
        result: set[str] = set()
        pending = [self.root]
        while pending:
            directory = pending.pop()
            # Do not let a recursive glob silently skip an inaccessible directory.
            entries = self._io(lambda: list(directory.iterdir()), directory)
            for path in entries:
                if path.name.startswith("."):
                    continue
                mode = self._io(path.lstat, path).st_mode
                if stat.S_ISLNK(mode):
                    continue
                if stat.S_ISDIR(mode):
                    pending.append(path)
                    continue
                relative = path.relative_to(self.root).as_posix()
                if stat.S_ISREG(mode) and _allowed_object(relative):
                    result.add(relative)
        progress("preparing", object_count=len(result), error=None)
        return result

    def upload(self, local_path: Path, relative: str) -> None:
        target = self.root / relative
        def copy():
            if not target.exists():
                atomic_write(target, local_path.read_bytes())
        self._io(copy, target)

    def download(self, relative: str, local_path: Path) -> None:
        source = self.root / relative
        try:
            payload = self._io(source.read_bytes, source)
        except FileNotFoundError:
            _wait_for_incomplete_icloud_item(self, relative)
            raise
        atomic_write(local_path, payload)

    def wait_for_item(self, relative: str) -> str | None:
        if not self.icloud:
            return None
        path = self.root / relative
        message = (
            "iCloud Drive prépare encore un fichier de sauvegarde. "
            "La synchronisation reprendra automatiquement dès que son contenu sera disponible."
        )
        self.progress("waiting_local_cloud", error=message)
        # Request both paths: a file can be absent from a partial directory
        # listing even though its directory is already present locally.
        _request_icloud_download(path.parent)
        _request_icloud_download(path)
        return message


def _request_icloud_download(path: Path) -> None:
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSFileManager, NSURL
        NSFileManager.defaultManager().startDownloadingUbiquitousItemAtURL_error_(
            NSURL.fileURLWithPath_(str(path)), None
        )
    except Exception:
        # Finder can still download the item if the native request is unavailable.
        pass


class RcloneArchiveRemote:
    def __init__(self, paths, provider: str, remote: str):
        self.paths = paths
        self.provider = provider
        self.remote = remote
        self.config = paths.home / "credentials" / "rclone.conf"

    def list(self, progress) -> set[str]:
        output = self._run(["lsf", self.remote, "--recursive", "--files-only"], timeout=600)
        objects = {line.strip() for line in output.splitlines() if _allowed_object(line.strip())}
        progress("preparing", object_count=len(objects))
        return objects

    def upload(self, local_path: Path, relative: str) -> None:
        self._run(["copyto", str(local_path), f"{self.remote}/{relative}"], timeout=600)

    def download(self, relative: str, local_path: Path) -> None:
        self._run(["copyto", f"{self.remote}/{relative}", str(local_path)], timeout=600)

    def _run(self, args: list[str], timeout: int) -> str:
        result = subprocess.run(
            [
                rclone_binary(),
                "--config",
                str(self.config),
                "--cache-dir",
                str(self.paths.cache / "rclone"),
                "--contimeout",
                "15s",
                "--timeout",
                "60s",
                "--retries",
                "2",
                *args,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode:
            if self.provider == "google-drive":
                from aimemory.cloud.google_drive import _friendly_google_error

                raise RuntimeError(_friendly_google_error(result.stderr, result.returncode))
            from aimemory.cloud.rclone_provider import _friendly_rclone_error

            raise RuntimeError(_friendly_rclone_error(self.provider, result.stderr, result.returncode))
        return result.stdout
