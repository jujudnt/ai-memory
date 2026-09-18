from __future__ import annotations

from aimemory.db import MemoryDatabase


class SearchService:
    def __init__(self, db: MemoryDatabase):
        self.db = db

    def search_conversations(
        self,
        query: str,
        source: str | None = None,
        project_id: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        return self.db.search(query=query, source=source, project_id=project_id, limit=limit)

    def list_conversations(
        self,
        source: str | None = None,
        project_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return self.db.list_conversations(source=source, project_id=project_id, limit=limit)
