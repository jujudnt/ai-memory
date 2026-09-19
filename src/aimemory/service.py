from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from filelock import FileLock

from aimemory.adapters import ClaudeAdapter, CodexAdapter, VSCodeAdapter
from aimemory.adapters.base import ConversationSourceAdapter
from aimemory.archive import JsonArchive
from aimemory.archive.raw_backup import write_raw
from aimemory.cleanup import run_auto_cleanup
from aimemory.cloud.providers import provider_label
from aimemory.config import AppPaths
from aimemory.db import MemoryDatabase
from aimemory.db.sqlite import file_hash
from aimemory.models import NormalizedConversation
from aimemory.search import SearchService
from aimemory.state import atomic_write, folder_bytes, now, read_json, write_json


@dataclass(slots=True)
class ImportResult:
    scanned: int
    imported: int
    skipped: int
    errors: list[dict] = field(default_factory=list)


class MemoryService:
    def __init__(self, paths: AppPaths | None = None):
        self.paths = paths or AppPaths.from_env()
        self.paths.ensure()
        run_auto_cleanup(self.paths)
        self.archive = JsonArchive(self.paths.archive)
        self.db = MemoryDatabase(self.paths.db / "memory.sqlite")
        self.search_service = SearchService(self.db)
        self.lock = FileLock(str(self.paths.state / "operations.lock"), timeout=1)

    def import_codex(self, codex_home: Path | None = None, force: bool = False) -> ImportResult:
        with self.lock:
            return self._import_adapter(CodexAdapter(codex_home=codex_home), force=force)

    def import_claude(self, claude_home: Path | None = None, force: bool = False) -> ImportResult:
        with self.lock:
            return self._import_adapter(ClaudeAdapter(claude_home=claude_home), force=force)

    def import_vscode(self, code_user_dir: Path | None = None, force: bool = False) -> ImportResult:
        with self.lock:
            return self._import_adapter(VSCodeAdapter(code_user_dir=code_user_dir), force=force)

    def import_all(self, force: bool = False, codex_home: Path | None = None) -> ImportResult:
        with self.lock:
            result = ImportResult(scanned=0, imported=0, skipped=0, errors=[])
            for adapter in (CodexAdapter(codex_home=codex_home), ClaudeAdapter(), VSCodeAdapter()):
                partial = self._import_adapter(adapter, force=force)
                result.scanned += partial.scanned
                result.imported += partial.imported
                result.skipped += partial.skipped
                result.errors.extend(partial.errors)
            return result

    def _import_adapter(self, adapter: ConversationSourceAdapter, force: bool) -> ImportResult:
        sessions = adapter.scan_sessions()
        manifest_path = self.paths.state / "source-manifest.json"
        manifest = read_json(manifest_path)
        synced_objects = read_json(self.paths.state / "synced-objects.json")
        imported = 0
        skipped = 0
        errors = []
        for session in sessions:
            entry = manifest.get(str(session.path), {})
            if (
                not force
                and entry.get("size") == session.size
                and entry.get("mtime_ns") == session.path.stat().st_mtime_ns
                and entry.get("parser_version") == getattr(adapter, "parser_version", 2)
                and (
                    (self.paths.archive / entry.get("raw_path", "missing")).is_file()
                    or bool(synced_objects.get(entry.get("raw_path", "")))
                )
                and self.db.get_conversation_row(entry.get("conversation_id", ""))
            ):
                skipped += 1
                continue
            try:
                stat = session.path.stat()
                raw = session.path.read_bytes()
                stat_hash = hashlib.sha256(raw).hexdigest()
                conversation = adapter.parse_session(session.path, raw)
                raw_path = write_raw(self.paths.archive, conversation.id, raw, stat_hash, entry)
                conversation.metadata.update(source_sha256=stat_hash, raw_path=raw_path.as_posix())
                # Preserve every source variant; the active index selects the latest revision.
                self.accept_conversation(conversation)
                with self.db.connect() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO imports(source, source_path, source_session_id, file_hash, size, mtime, last_offset) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (adapter.source, str(session.path), conversation.source_session_id, stat_hash, len(raw), stat.st_mtime, len(raw)),
                    )
                manifest[str(session.path)] = {
                    "conversation_id": conversation.id, "source_session_id": conversation.source_session_id,
                    "sha256": stat_hash, "raw_path": raw_path.as_posix(), "size": len(raw),
                    "mtime_ns": stat.st_mtime_ns, "parser_version": getattr(adapter, "parser_version", 2),
                    "messages": len(conversation.messages), "tool_calls": len(conversation.tool_calls),
                }
                imported += 1
            except ValueError as exc:
                if "session is empty" in str(exc):
                    skipped += 1
                    continue
                errors.append({"path": str(session.path), "error": str(exc)})
            except Exception as exc:
                errors.append({"path": str(session.path), "error": str(exc)})
            write_json(manifest_path, manifest)
        return ImportResult(scanned=len(sessions), imported=imported, skipped=skipped, errors=errors)

    def accept_conversation(self, conversation: NormalizedConversation) -> bool:
        existing = self.get_conversation(conversation.id)
        def revision_key(value):
            digest = hashlib.sha256(json.dumps(value.to_dict(), sort_keys=True).encode()).hexdigest()
            return (value.metadata.get("parser_version", 0), value.updated_at or "",
                    len(value.messages) + len(value.tool_calls), digest)
        if existing and revision_key(existing) > revision_key(conversation):
            # Keep a divergent/older revision without replacing the current index.
            from tempfile import TemporaryDirectory
            with TemporaryDirectory(dir=self.paths.cache) as directory:
                temp_archive = JsonArchive(Path(directory))
                path = temp_archive.write(conversation)
                self.archive.preserve(path, conversation.id)
            return False
        path = self.archive.write(conversation)
        self.db.upsert_conversation(conversation, path)
        return True

    def sync_now(self) -> dict:
        from aimemory.sync.cloud_sync import CloudSync
        return CloudSync(self).run()

    def index_archive(self, path: Path) -> NormalizedConversation:
        conversation = self.archive.read(path)
        self.db.upsert_conversation(conversation, archive_path=path)
        return conversation

    def rebuild_from_archives(self) -> int:
        count = 0
        for path in self.archive.iter_archives():
            self.index_archive(path)
            count += 1
        return count

    def search(
        self,
        query: str,
        source: str | None = None,
        project_id: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        return self.search_service.search_conversations(query, source, project_id, limit)

    def get_conversation(self, conversation_id: str) -> NormalizedConversation | None:
        row = self.db.get_conversation_row(conversation_id)
        if not row:
            return None
        return self.archive.read(Path(row["archive_path"]))

    def list_conversations(
        self,
        source: str | None = None,
        project_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return self.db.list_conversations(source=source, project_id=project_id, limit=limit)

    def status(self) -> dict:
        status = self.db.status()
        status["home"] = str(self.paths.home)
        status["archive"] = str(self.paths.archive)
        status["archive_count"] = len(self.archive.iter_archives())
        config = read_json(self.paths.state / "cloud.json")
        status["storage"] = {
            "provider": config.get("provider", "local-folder"),
            "provider_label": provider_label(config.get("provider")),
            "home": str(self.paths.home),
            "archive": str(self.paths.archive),
            "cloud_sync": "configured" if config else "not-configured",
            "archive_bytes": folder_bytes(self.paths.archive),
            "normalized_bytes": folder_bytes(self.paths.archive / "sources"),
            "raw_bytes": folder_bytes(self.paths.archive / "raw"),
            "revisions_bytes": folder_bytes(self.paths.archive / "snapshots"),
            "database_bytes": sum(p.stat().st_size for p in self.paths.db.glob("memory.sqlite*")),
            "total_bytes": folder_bytes(self.paths.home),
            "remote": config.get("root", "AI-Memory"),
        }
        status["sync"] = read_json(self.paths.state / "sync-status.json")
        status["audit"] = read_json(self.paths.state / "audit.json")
        status["cleanup"] = read_json(self.paths.state / "cleanup-status.json")
        status["retention"] = read_json(self.paths.state / "retention-status.json")
        status["watcher"] = self.watcher_status()
        return status

    def watcher_status(self) -> dict | None:
        status_path = self.paths.state / "watcher-status.json"
        if not status_path.exists():
            return None
        result = read_json(status_path)
        from filelock import Timeout
        try:
            with FileLock(str(self.paths.state / "watcher.lock"), timeout=0):
                result["running"] = False
        except Timeout:
            result["running"] = True
        return result

    def audit_codex(self, codex_home: Path | None = None) -> dict:
        from aimemory.audit import audit_codex
        with self.lock:
            return audit_codex(self, codex_home)
