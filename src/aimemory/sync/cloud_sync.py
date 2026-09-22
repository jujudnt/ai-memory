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
import threading
import uuid
from pathlib import Path
from filelock import FileLock

from aimemory.cloud.google_drive import rclone_binary
from aimemory.cloud.providers import LOCAL_FOLDER_PROVIDERS, RCLONE_DIRECT_PROVIDERS, validate_sync_root
from aimemory.cloud.rclone_provider import RcloneCloudProvider
from aimemory.cloud.destination import remote_path
from aimemory.cloud.errors import CloudError, error_category
from aimemory.archive.json_archive import JsonArchive
from aimemory.archive.raw_backup import RAW_PATTERN, RawIntegrityError, load_delta, validate_raw
from aimemory.state import atomic_write, now, read_json, write_json
from aimemory.sources import CODEX_SOURCES

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
            status.update(status="preparing", started_at=now(), error=None, message=None,
                          error_category=None, retry_after=None, requires_action=False,
                          transfers=0, totalTransfers=0, confirmedBefore=0,
                          bytes=0, totalBytes=0, speed=0)
            status_lock = threading.RLock()
            last_beat = [0.0]
            def progress(phase, **details):
                check_cancelled(self.paths)
                with status_lock:
                    status.update(status=phase, heartbeat_at=now(), **details)
                    if "error" not in details:
                        status["error"] = None
                    write_json(self.status_path, status)
            def heartbeat():
                check_cancelled(self.paths)
                if time.monotonic() - last_beat[0] < 2:
                    return
                last_beat[0] = time.monotonic()
                with status_lock:
                    progress(status["status"])
            try:
                progress("preparing")
                remote = _remote_archive(self.paths, config)
                remote.heartbeat = heartbeat
                known = read_json(self.paths.state / "synced-objects.json")
                remote_identity = _remote_identity(config)
                previous_identity = known.pop("__remote_identity__", None)
                destination_changed = bool(previous_identity and previous_identity != remote_identity)
                if destination_changed:
                    known = read_json(self.paths.state / "destinations" / f"{hashlib.sha256(remote_identity.encode()).hexdigest()}.json")
                def checkpoint():
                    saved = {**known, "__remote_identity__": remote_identity}
                    write_json(self.paths.state / "synced-objects.json", saved)
                    write_json(self.paths.state / "destinations" / f"{hashlib.sha256(remote_identity.encode()).hexdigest()}.json", saved)
                remote_objects = remote.list(progress)
                current_sources = _current_source_paths(self.service)
                redundant_snapshots = set() if pull_only or (self.paths.state / "migration.json").exists() else _redundant_snapshot_objects(self.paths, current_sources)
                remote_redundant = redundant_snapshots & remote_objects
                usable_remote_objects = remote_objects - remote_redundant
                known = {relative: True for relative, confirmed in known.items()
                         if confirmed and relative in usable_remote_objects}
                migration_path = self.paths.state / "migration.json"
                migration = read_json(migration_path)
                if pull_only:
                    migration = {"status": "preparing", "source_identity": remote_identity,
                                 "objects": sorted(usable_remote_objects), "started_at": now()}
                    write_json(migration_path, migration)
                local_objects = list(
                    _iter_local_objects(
                        self.paths.archive,
                        include_current_sources=True,
                        current_source_paths=current_sources,
                        excluded=redundant_snapshots,
                        already_synced=set(known),
                    )
                )
                uploads = [] if pull_only else [
                    (relative, path) for relative, path in local_objects if relative not in usable_remote_objects
                ]
                available = usable_remote_objects | {relative for relative, _ in uploads}
                for relative, path in uploads:
                    if relative.endswith(".delta.json.gz"):
                        with gzip.open(path, "rb") as stream:
                            parent = json.load(stream).get("parent")
                        if parent not in available:
                            raise CloudError("Une base de sauvegarde manque sur cette destination. Restaurez l'ancienne destination avant de migrer l'historique.", "integrity")
                if uploads and config["provider"] in LOCAL_FOLDER_PROVIDERS:
                    _ensure_available_space_for_files((path for _, path in uploads), remote.root)
                if uploads and hasattr(remote, "upload_many"):
                    completed = 0
                    for start in range(0, len(uploads), 50):
                        check_cancelled(self.paths)
                        batch = uploads[start:start + 50]
                        progress(
                            "uploading",
                            object_count=len(usable_remote_objects),
                            transfers=completed,
                            totalTransfers=len(uploads),
                        )
                        remote.upload_many(batch)
                        for relative, _ in batch:
                            usable_remote_objects.add(relative)
                            known[relative] = True
                        checkpoint()
                        completed += len(batch)
                        progress(
                            "uploading",
                            object_count=len(usable_remote_objects),
                            transfers=completed,
                            totalTransfers=len(uploads),
                        )
                else:
                    for index, (relative, path) in enumerate(uploads, start=1):
                        check_cancelled(self.paths)
                        progress("uploading", object_count=len(usable_remote_objects), transfers=index - 1, totalTransfers=len(uploads))
                        remote.upload(path, relative)
                        usable_remote_objects.add(relative)
                        known[relative] = True
                        checkpoint()
                        progress("uploading", object_count=len(usable_remote_objects), transfers=index,
                                 totalTransfers=len(uploads))
                if not uploads:
                    progress("uploading", object_count=len(usable_remote_objects))
                # Reclaim already verified iCloud copies before receiving more history.
                # A large or interrupted download must not postpone retention forever.
                retention = {}
                if (config["provider"] == "icloud-drive" and not pull_only and not migration
                        and any(key.startswith("snapshots/") for key in known)):
                    with self.service.lock.acquire(timeout=60):
                        retention = prune_synced_local_copies(
                            self.paths, {key: value for key, value in known.items() if key.startswith("snapshots/")},
                            confirm_copy=remote.confirm_uploaded_copy,
                            heartbeat=heartbeat,
                        )
                if remote_redundant and not pull_only:
                    check_cancelled(self.paths)
                    remote.delete_many(remote_redundant)
                    for relative in remote_redundant:
                        known.pop(relative, None)
                progress("verifying")
                indexed = 0
                verified_raw = {}
                incoming = self.paths.cache / "incoming"
                downloads = sorted(
                    (path for path in usable_remote_objects
                     if path not in known or (pull_only and not (self.paths.archive / path).is_file())),
                    key=lambda path: (not path.startswith("snapshots/"), path),
                )
                confirmed_before = len(known)
                for index, relative_string in enumerate(downloads, start=1):
                    check_cancelled(self.paths)
                    relative = Path(relative_string)
                    if not _allowed_object(relative_string):
                        raise ValueError("Unexpected object in cloud archive")
                    progress("downloading", object_count=len(known), transfers=index - 1,
                             totalTransfers=len(downloads), confirmedBefore=confirmed_before)
                    if hasattr(remote, "download_many") and not ((self.paths.archive if relative.parts[0] == "raw" else incoming) / relative).is_file():
                        batch = [item for item in downloads[index - 1:index + 19]
                                 if item.startswith(relative.parts[0] + "/")]
                        root = self.paths.archive if relative.parts[0] == "raw" else incoming
                        batch = [item for item in batch if not (root / item).is_file()]
                        remote.download_many(batch, root)
                    # Raw dependencies live directly in the archive: never duplicate a
                    # whole history in both incoming/ and archive/ during a transfer.
                    path = (self.paths.archive if relative.parts[0] == "raw" else incoming) / relative
                    _check_download_space(self.paths)
                    if not path.is_file():
                        remote.download(relative_string, path)
                    if relative.parts[0] == "snapshots":
                        payload = path.read_bytes()
                        if hashlib.sha256(payload).hexdigest() != path.name.split(".")[0]:
                            path.unlink()
                            _wait_for_incomplete_icloud_item(remote, relative_string)
                            raise ValueError("Cloud archive checksum mismatch. Retry synchronization.")
                        conversation = self.service.archive.read(path)
                        if conversation.id != relative.parts[1]:
                            raise ValueError("Cloud archive identity mismatch")
                        # Skip already present local snapshots; remote revisions are indexed.
                        legacy_codex = conversation.source in CODEX_SOURCES and conversation.metadata.get("parser_version", 0) < 2
                        if not legacy_codex:
                            with self.service.lock.acquire(timeout=60):
                                indexed += int(self.service.accept_conversation(conversation))
                    else:
                        try:
                            _hydrate_raw_dependencies(
                                self.paths.archive,
                                relative_string,
                                remote,
                                usable_remote_objects,
                            )
                            validate_raw(self.paths.archive, relative.as_posix(), verified_raw, heartbeat)
                        except CloudFolderPending:
                            raise
                        except (gzip.BadGzipFile, EOFError, ValueError, KeyError, json.JSONDecodeError) as exc:
                            invalid = self.paths.archive / exc.relative if isinstance(exc, RawIntegrityError) else path
                            invalid.unlink(missing_ok=True)
                            _wait_for_incomplete_icloud_item(remote, relative_string)
                            raise ValueError("Raw cloud backup checksum mismatch. Retry synchronization.")
                    target = self.paths.archive / relative
                    if path != target:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(path, target)
                    known[relative_string] = True
                    checkpoint()
                    progress("downloading", object_count=len(known), transfers=index,
                             totalTransfers=len(downloads), confirmedBefore=confirmed_before)
                    if len(known) % 10 == 0:
                        progress("verifying", object_count=len(known))
                for relative_string, _ in local_objects:
                    if relative_string in usable_remote_objects:
                        known[relative_string] = True
                checkpoint()
                # Old versions staged raw files twice. Remove only a verified
                # duplicate, never the sole unconfirmed copy or a migration file.
                for relative_string in known:
                    staged = incoming / relative_string
                    if staged.is_file() and (self.paths.archive / relative_string).is_file():
                        staged.unlink()
                migration_complete = (
                    migration.get("source_identity") != remote_identity
                    and set(migration.get("objects", [])) <= usable_remote_objects
                )
                if pull_only:
                    migration.update(status="prepared", prepared_at=now())
                    write_json(migration_path, migration)
                elif migration and migration_complete:
                    migration_path.unlink(missing_ok=True)
                # A local cloud folder is not proof of upload to the provider.
                can_prune = config["provider"] not in {"icloud-drive", "onedrive", "dropbox"}
                if can_prune and not pull_only and (not migration or migration_complete):
                    with self.service.lock.acquire(timeout=60):
                        retention = prune_synced_local_copies(
                            self.paths, known, redundant_snapshots=redundant_snapshots,
                            current_source_paths=_current_source_paths(self.service),
                        )
                elif config["provider"] == "icloud-drive" and known and not pull_only and (not migration or migration_complete):
                    with self.service.lock.acquire(timeout=60):
                        final_retention = prune_synced_local_copies(
                            self.paths, known, confirm_copy=remote.confirm_uploaded_copy,
                            heartbeat=heartbeat,
                        )
                    for key in ("freed_bytes", "removed_count", "removed_raw_count", "removed_revision_count"):
                        final_retention[key] += retention.get(key, 0)
                    retention = final_retention
                    write_json(self.paths.state / "retention-status.json", retention)
                write_json(
                    self.paths.state / "semantic-cleanup.json",
                    {"version": 1, "completed_at": now(), "removed_count": len(redundant_snapshots)},
                )
                status.update(status="synced", last_success_at=now(), indexed=indexed,
                              object_count=len(usable_remote_objects), retention=retention, error=None,
                              failures=0, retry_after=None,
                              confirmation="local_folder" if not can_prune else "remote")
            except CloudFolderPending as exc:
                status.update(status="waiting_local_cloud", error=str(exc), heartbeat_at=now(),
                              requires_action=False, message=str(exc), retry_after=time.time() + 15)
            except Exception as exc:
                # Resolution, disk-space checks and cleanup can hit a busy cloud
                # placeholder too, before/after LocalArchiveRemote's read retries.
                if (isinstance(exc, OSError) and exc.errno in {errno.EDEADLK, errno.EAGAIN, errno.EBUSY}
                        and config.get("provider") in LOCAL_FOLDER_PROVIDERS):
                    message = "Le dossier cloud prépare encore un fichier. Nouvelle tentative automatique dans quelques secondes."
                    status.update(status="waiting_local_cloud", error=message, heartbeat_at=now(),
                                  requires_action=False, message=message, retry_after=time.time() + 15)
                    return status
                category = getattr(exc, "category", "quota" if isinstance(exc, OSError) and exc.errno == errno.ENOSPC
                                   else "integrity" if isinstance(exc, ValueError) else "transient")
                failures = status.get("failures", 0) + 1
                status.update(status="paused" if isinstance(exc, SyncCancelled) else "error",
                              error=str(exc), failed_at=now(), error_category=category,
                              requires_action=category == "auth", failures=failures,
                              retry_after=time.time() + min(900, 30 * 2 ** min(failures, 5)))
                raise
            finally:
                write_json(self.status_path, status)
            return status


class SyncCancelled(RuntimeError):
    pass


def _check_download_space(paths, required: int = 0) -> None:
    if shutil.disk_usage(paths.home).free < required + 256 * 1024 * 1024:
        raise CloudError("Espace local insuffisant pour recevoir les archives. Libérez de l'espace puis reprenez.", "quota")


def cancel_active_sync(paths) -> None:
    write_json(paths.state / "sync-cancel.json", {"cancelled_at": now()})


def check_cancelled(paths) -> None:
    if (paths.state / "sync-cancel.json").exists():
        raise SyncCancelled("Synchronisation suspendue. Les copies validées sont conservées.")


def clear_cancel(paths) -> None:
    (paths.state / "sync-cancel.json").unlink(missing_ok=True)


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


def prune_synced_local_copies(
    paths,
    known: dict,
    redundant_snapshots: set[str] | None = None,
    current_source_paths: set[Path] | None = None,
    confirm_copy=None,
    heartbeat=lambda: None,
) -> dict:
    removable_roots = ("raw/", "snapshots/")
    freed = 0
    removed: list[str] = []
    for relative in sorted(path for path in known if path.startswith(removable_roots) and _allowed_object(path)):
        heartbeat()
        target = paths.archive / relative
        if not known[relative] or not target.is_file() or target.resolve() != paths.archive.resolve() / relative:
            continue
        try:
            if confirm_copy is not None and not confirm_copy(relative, target):
                continue
            size = target.stat().st_size
            target.unlink()
            freed += size
            removed.append(relative)
            _prune_empty_parents(target.parent, paths.archive)
        except OSError:
            continue
    for relative in sorted(redundant_snapshots or set()):
        target = paths.archive / relative
        if not target.is_file():
            continue
        freed += target.stat().st_size
        target.unlink()
        removed.append(relative)
        _prune_empty_parents(target.parent, paths.archive)
    removed_sources = 0
    if current_source_paths is not None:
        current_ids = {path.name.split(".json.", 1)[0] for path in current_source_paths}
        for target in sorted((paths.archive / "sources").rglob("*.json.*")):
            if target.resolve() in current_source_paths:
                continue
            if target.name.split(".json.", 1)[0] not in current_ids:
                continue
            freed += target.stat().st_size
            target.unlink()
            removed_sources += 1
            _prune_empty_parents(target.parent, paths.archive)
    status = {
        "last_run_at": now(),
        "freed_bytes": freed,
        "removed_count": len(removed) + removed_sources,
        "removed_raw_count": sum(1 for path in removed if path.startswith("raw/")),
        "removed_revision_count": sum(1 for path in removed if path.startswith("snapshots/")),
        "removed_source_count": removed_sources,
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


def _iter_local_objects(
    archive: Path,
    include_current_sources: bool = False,
    current_source_paths: set[Path] | None = None,
    excluded: set[str] | None = None,
    already_synced: set[str] | None = None,
):
    seen: set[str] = set()
    excluded = excluded or set()
    # Send one immutable current revision per conversation before its history.
    if include_current_sources:
        for path in sorted((archive / "sources").rglob("*.json.*")):
            if not path.is_file() or path.is_symlink() or path.name.startswith("."):
                continue
            if current_source_paths is not None and path.resolve() not in current_source_paths:
                continue
            suffix = ".json.zst" if path.name.endswith(".json.zst") else ".json.gz"
            conversation_id = path.name.removesuffix(suffix)
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", conversation_id):
                raise ValueError("Unexpected current conversation archive")
            payload = path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            relative = f"snapshots/{conversation_id}/{digest}{suffix}"
            if relative in (already_synced or set()):
                continue  # Do not recreate a purged revision every minute.
            target = archive / relative
            if not target.exists():
                atomic_write(target, payload)
            if relative in excluded:
                continue
            seen.add(relative)
            yield relative, target
    for category in ("snapshots", "raw"):
        for path in sorted((archive / category).rglob("*")):
            if path.is_file() and not path.is_symlink() and not path.name.startswith("."):
                relative = path.relative_to(archive).as_posix()
                if not _allowed_object(relative):
                    raise ValueError("Unexpected object in local archive")
                if relative not in seen and relative not in excluded:
                    seen.add(relative)
                    yield relative, path


def _current_source_paths(service) -> set[Path]:
    if not hasattr(service, "db"):
        return set()
    return {
        Path(item["archive_path"]).resolve()
        for item in service.db.archive_entries()
        if item.get("archive_path")
    }


def _redundant_snapshot_objects(paths, current_source_paths: set[Path]) -> set[str]:
    state_path = paths.state / "semantic-cleanup.json"
    state = read_json(state_path)
    if state.get("version") == 1 and state.get("completed_at"):
        return set()
    if state.get("version") == 1 and state.get("pending"):
        return set(state["pending"])

    archive = JsonArchive(paths.archive)
    preferred: set[str] = set()
    for source in current_source_paths:
        if not source.is_file():
            continue
        payload = source.read_bytes()
        preferred.add(
            f"snapshots/{source.name.split('.json.', 1)[0]}/"
            f"{hashlib.sha256(payload).hexdigest()}{''.join(source.suffixes)}"
        )

    groups: dict[str, list[str]] = {}
    for path in sorted((paths.archive / "snapshots").rglob("*.json.*")):
        try:
            conversation = archive.read(path)
        except Exception:
            continue
        value = conversation.to_dict()
        semantic = {
            "schema_version": value["schema_version"],
            "id": value["id"],
            "source_session_id": value["source_session_id"],
            "created_at": value["created_at"],
            "updated_at": value["updated_at"],
            "project": value["project"],
            "model": value["model"],
            "title": value["title"],
            "messages": value["messages"],
            "tool_calls": value["tool_calls"],
            "files_referenced": value["files_referenced"],
            "commands": value["commands"],
        }
        fingerprint = hashlib.sha256(
            json.dumps(semantic, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        groups.setdefault(fingerprint, []).append(path.relative_to(paths.archive).as_posix())

    redundant: set[str] = set()
    for relatives in groups.values():
        if len(relatives) < 2:
            continue
        keeper = next((relative for relative in relatives if relative in preferred), min(relatives))
        redundant.update(relative for relative in relatives if relative != keeper)
    write_json(state_path, {"version": 1, "pending": sorted(redundant), "created_at": now()})
    return redundant


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
        if hasattr(remote, "heartbeat"):
            remote.heartbeat()
        payload = load_delta(incoming, current)
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

    def confirm_uploaded_copy(self, relative: str, local_path: Path) -> bool:
        """Confirm a previously verified immutable object, without hydrating iCloud.

        The caller must restrict this to the current destination's successful sync
        checkpoints. An evicted object is already in iCloud: reading it again would
        download the whole backup just to free its redundant local copy.
        """
        if not self.icloud or not _allowed_object(relative):
            return False
        target = self.root / relative
        try:
            if target.resolve() != self.root.resolve() / relative or not target.is_file():
                return False
            state = _icloud_upload_state(target)
            if not state.get("uploaded") or state.get("size") != local_path.stat().st_size:
                return False
            # Resident files can additionally be compared byte for byte. Do not
            # fetch an evicted object; the successful checkpoint + native upload
            # confirmation identify that immutable cloud copy.
            if state.get("resident"):
                with local_path.open("rb") as local, target.open("rb") as cloud:
                    while True:
                        if hasattr(self, "heartbeat"):
                            self.heartbeat()
                        chunk = local.read(1024 * 1024)
                        if chunk != cloud.read(1024 * 1024):
                            return False
                        if not chunk:
                            break
            return bool(_icloud_upload_state(target).get("uploaded"))
        except OSError:
            return False

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
                _stream_copy_atomic(local_path, target)
        self._io(copy, target)

    def download(self, relative: str, local_path: Path) -> None:
        source = self.root / relative
        try:
            self._io(lambda: _stream_copy_atomic(source, local_path), source)
        except FileNotFoundError:
            _wait_for_incomplete_icloud_item(self, relative)
            raise

    def delete_many(self, relatives: set[str]) -> None:
        for relative in sorted(relatives):
            target = self.root / relative
            if target.exists():
                self._io(target.unlink, target)
                _prune_empty_parents(target.parent, self.root)

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


def _icloud_upload_state(path: Path) -> dict:
    """Fail closed when Foundation, metadata, or upload confirmation is absent."""
    if sys.platform != "darwin":
        return {}
    try:
        from Foundation import (
            NSURL, NSURLIsUbiquitousItemKey, NSURLFileSizeKey,
            NSURLUbiquitousItemIsUploadedKey, NSURLUbiquitousItemIsUploadingKey,
            NSURLUbiquitousItemUploadingErrorKey, NSURLUbiquitousItemDownloadingStatusKey,
            NSURLUbiquitousItemDownloadingStatusCurrent,
        )
        keys = [NSURLIsUbiquitousItemKey, NSURLFileSizeKey,
                NSURLUbiquitousItemIsUploadedKey, NSURLUbiquitousItemIsUploadingKey,
                NSURLUbiquitousItemUploadingErrorKey, NSURLUbiquitousItemDownloadingStatusKey]
        values, error = NSURL.fileURLWithPath_(str(path)).resourceValuesForKeys_error_(keys, None)
        if error or not values:
            return {}
        return {
            "uploaded": bool(values.get(NSURLIsUbiquitousItemKey)
                             and values.get(NSURLUbiquitousItemIsUploadedKey)
                             and values.get(NSURLUbiquitousItemIsUploadingKey) is not None
                             and not values.get(NSURLUbiquitousItemIsUploadingKey)
                             and not values.get(NSURLUbiquitousItemUploadingErrorKey)),
            "size": values.get(NSURLFileSizeKey),
            "resident": values.get(NSURLUbiquitousItemDownloadingStatusKey) == NSURLUbiquitousItemDownloadingStatusCurrent,
        }
    except Exception:
        return {}


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


def _stream_copy_atomic(source: Path, target: Path) -> None:
    """Copy large cloud objects without holding their full contents in RAM."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
    try:
        with source.open("rb") as incoming, temporary.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


class RcloneArchiveRemote:
    def __init__(self, paths, provider: str, remote: str):
        self.paths = paths
        self.provider = provider
        self.remote = remote
        self.config = paths.home / "credentials" / "rclone.conf"
        self.heartbeat = lambda: None
        self.sizes = {}

    def list(self, progress) -> set[str]:
        output = self._run(["lsjson", self.remote, "--recursive", "--files-only"], timeout=600)
        entries = json.loads(output)
        self.sizes = {entry["Path"]: max(0, entry.get("Size", 0)) for entry in entries
                      if _allowed_object(entry.get("Path", ""))}
        objects = set(self.sizes)
        progress("preparing", object_count=len(objects))
        return objects

    def upload(self, local_path: Path, relative: str) -> None:
        self._run(["copyto", str(local_path), f"{self.remote}/{relative}"], timeout=600)

    def upload_many(self, uploads: list[tuple[str, Path]]) -> None:
        selection = self.paths.cache / "cloud-upload-objects.txt"
        atomic_write(
            selection,
            ("\n".join(relative for relative, _ in uploads) + "\n").encode("utf-8"),
        )
        try:
            self._run(
                ["copy", str(self.paths.archive), self.remote, "--files-from", str(selection)],
                timeout=1800,
            )
        finally:
            selection.unlink(missing_ok=True)

    def download(self, relative: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        partial = local_path.with_name("." + local_path.name + ".partial")
        try:
            _check_download_space(self.paths, self.sizes.get(relative, 0))
            self._run(["copyto", f"{self.remote}/{relative}", str(partial)], timeout=600)
            os.replace(partial, local_path)
        finally:
            partial.unlink(missing_ok=True)

    def download_many(self, relatives: list[str], destination: Path) -> None:
        if not relatives:
            return
        _check_download_space(self.paths, sum(self.sizes.get(item, 0) for item in relatives))
        selection = self.paths.cache / "cloud-download-objects.txt"
        atomic_write(selection, ("\n".join(relatives) + "\n").encode())
        try:
            self._run(["copy", self.remote, str(destination), "--files-from", str(selection),
                       "--ignore-existing", "--transfers", "2"], timeout=1800)
        finally:
            selection.unlink(missing_ok=True)

    def delete_many(self, relatives: set[str]) -> None:
        selection = self.paths.cache / "redundant-cloud-objects.txt"
        atomic_write(selection, ("\n".join(sorted(relatives)) + "\n").encode("utf-8"))
        try:
            self._run(["delete", self.remote, "--files-from", str(selection)], timeout=600)
        finally:
            selection.unlink(missing_ok=True)

    def _run(self, args: list[str], timeout: int) -> str:
        process = subprocess.Popen(
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        started = time.monotonic()
        try:
            while True:
                check_cancelled(self.paths)
                self.heartbeat()
                try:
                    stdout, stderr = process.communicate(timeout=2)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() - started > timeout:
                        raise CloudError("Le transfert a dépassé le délai. Reprise automatique prévue.")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        if process.returncode:
            if self.provider == "google-drive":
                from aimemory.cloud.google_drive import _friendly_google_error

                raise CloudError(_friendly_google_error(stderr, process.returncode), error_category(stderr))
            from aimemory.cloud.rclone_provider import _friendly_rclone_error

            raise CloudError(_friendly_rclone_error(self.provider, stderr, process.returncode), error_category(stderr))
        return stdout
