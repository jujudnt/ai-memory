from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aimemory.adapters import CodexAdapter
from aimemory.archive import JsonArchive
from aimemory.config import AppPaths
from aimemory.db import MemoryDatabase
from aimemory.db.sqlite import file_hash
from aimemory.models import NormalizedConversation
from aimemory.search import SearchService


@dataclass(slots=True)
class ImportResult:
    scanned: int
    imported: int
    skipped: int


class MemoryService:
    def __init__(self, paths: AppPaths | None = None):
        self.paths = paths or AppPaths.from_env()
        self.paths.ensure()
        self.archive = JsonArchive(self.paths.archive)
        self.db = MemoryDatabase(self.paths.db / "memory.sqlite")
        self.search_service = SearchService(self.db)

    def import_codex(self, codex_home: Path | None = None, force: bool = False) -> ImportResult:
        adapter = CodexAdapter(codex_home=codex_home)
        sessions = adapter.scan_sessions()
        imported = 0
        skipped = 0
        for session in sessions:
            stat_hash = file_hash(session.path)
            state = self.db.import_state(adapter.source, session.path)
            if (
                not force
                and state
                and state["file_hash"] == stat_hash
                and state["size"] == session.size
            ):
                skipped += 1
                continue
            conversation = adapter.parse_session(session.path)
            archive_path = self.archive.write(conversation)
            self.db.upsert_conversation(
                conversation,
                archive_path=archive_path,
                source_path=session.path,
                file_hash=stat_hash,
                size=session.size,
                mtime=session.mtime,
            )
            imported += 1
        return ImportResult(scanned=len(sessions), imported=imported, skipped=skipped)

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
        status["watcher"] = self.watcher_status()
        return status

    def watcher_status(self) -> dict | None:
        status_path = self.paths.state / "watcher-status.json"
        if not status_path.exists():
            return None
        import json

        return json.loads(status_path.read_text(encoding="utf-8"))
