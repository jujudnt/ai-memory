from __future__ import annotations

from aimemory.service import MemoryService


class MemoryToolHandlers:
    """Transport-neutral MCP tool handlers.

    The desktop installer can later bind these methods to the official MCP
    Python server without duplicating search or sync logic.
    """

    def __init__(self, service: MemoryService | None = None):
        self.service = service or MemoryService()

    def search_conversations(
        self,
        query: str,
        source: str = "all",
        project: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        del date_from, date_to
        source_filter = None if source == "all" else source
        return self.service.search(query=query, source=source_filter, project_id=project, limit=limit)

    def get_conversation(self, conversation_id: str) -> dict | None:
        conversation = self.service.get_conversation(conversation_id)
        return conversation.to_dict() if conversation else None

    def list_conversations(
        self,
        source: str = "all",
        project: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        source_filter = None if source == "all" else source
        return self.service.list_conversations(source=source_filter, project_id=project, limit=limit)

    def get_project_history(self, project: str, limit: int = 20) -> list[dict]:
        return self.service.list_conversations(project_id=project, limit=limit)

    def get_sync_status(self) -> dict:
        return self.service.status()

    def sync_now(self) -> dict:
        return self.service.sync_now()
