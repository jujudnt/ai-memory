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
        source_filter = None if source == "all" else source
        return self.service.db.search(query=query, source=source_filter, project_id=project,
                                      limit=limit, date_from=date_from, date_to=date_to)

    def get_conversation(self, conversation_id: str, offset: int = 0, limit: int = 100) -> dict | None:
        """Read a page of messages and tool calls; next_offset continues long sessions."""
        conversation = self.service.get_conversation(conversation_id)
        if not conversation:
            return None
        result = conversation.to_dict()
        offset, limit = max(0, offset), max(1, min(limit, 200))
        total = max(len(result["messages"]), len(result["tool_calls"]))
        result["message_count"] = len(result["messages"])
        result["tool_call_count"] = len(result["tool_calls"])
        result["messages"] = result["messages"][offset:offset + limit]
        result["tool_calls"] = result["tool_calls"][offset:offset + limit]
        result["next_offset"] = offset + limit if offset + limit < total else None
        return result

    def list_conversations(
        self,
        source: str = "all",
        project: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        source_filter = None if source == "all" else source
        return self.service.db.list_conversations(source=source_filter, project_id=project,
                                                  limit=max(1, min(limit, 200)), offset=offset)

    def list_projects(self) -> list[dict]:
        """List project names, IDs and paths; use the ID to disambiguate identical names."""
        return self.service.db.list_projects()

    def get_project_history(self, project: str, limit: int = 20) -> list[dict]:
        return self.service.list_conversations(project_id=project, limit=limit)

    def get_sync_status(self) -> dict:
        return self.service.status()

    def sync_now(self) -> dict:
        from aimemory.state import now, write_json
        write_json(self.service.paths.state / "sync-request.json", {"requested_at": now()})
        return {"status": "queued", "message": "Le watcher traitera la demande. Consultez get_sync_status.",
                "watcher_running": bool((self.service.watcher_status() or {}).get("running"))}
