from __future__ import annotations

from aimemory.service import MemoryService
from aimemory.adapters.common import stable_device_id
from aimemory.search.intent import search_intent, project_suggestions


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
        device: str | None = None,
    ) -> list[dict]:
        """Find conversations by keywords or a short French/English request.

        For 'Sabai on my other Mac', use project='Sabai', source='codex',
        device='other', query=''. Results identify device/project and last message.
        device also accepts 'current', a device ID or exact name. If a dictated
        project name is uncertain, call list_projects(query=...) for candidates;
        never silently assume a fuzzy match is the requested project.
        """
        query, source, project, device = search_intent(
            query, source, project, device, self.service.db.list_projects())
        source_filter = None if source == "all" else source
        if not query:
            return self.service.db.list_conversations(source=source_filter, project_id=project,
                limit=max(1, min(limit, 20)), date_from=date_from, date_to=date_to,
                device=device, current_device_id=stable_device_id())
        return self.service.db.search(query=query, source=source_filter, project_id=project,
                                      limit=max(1, min(limit, 20)), date_from=date_from, date_to=date_to,
                                      device=device, current_device_id=stable_device_id())

    def get_conversation(self, conversation_id: str, offset: int = 0, limit: int = 20,
                         latest: bool = False, include_tools: bool = False,
                         max_chars: int = 2000, include_context: bool = False) -> dict | None:
        """Read up to 20 bounded messages directly from the local index.

        Set latest=True for the newest exchanges; next_offset then goes backwards.
        Tools and system/developer context are excluded unless explicitly requested.
        Truncated entries are marked. Use get_message to continue a long message.
        """
        return self.service.db.conversation_page(conversation_id, offset, limit, latest,
                                                 include_tools, max_chars, include_context)

    def get_message(self, conversation_id: str, message_id: str, offset: int = 0,
                    limit: int = 8000, field: str = "content") -> dict | None:
        """Read a bounded continuation of one indexed message or tool output."""
        return self.service.db.message_excerpt(conversation_id, message_id, offset, limit, field)

    def list_conversations(
        self,
        source: str = "all",
        project: str | None = None,
        limit: int = 50,
        offset: int = 0,
        device: str | None = None,
    ) -> list[dict]:
        source_filter = None if source == "all" else source
        return self.service.db.list_conversations(source=source_filter, project_id=project,
                                                  limit=max(1, min(limit, 50)), offset=offset,
                                                  device=device, current_device_id=stable_device_id())

    def list_projects(self, query: str = "") -> list[dict]:
        """List names/IDs/paths, or suggest similar names for a typo or voice transcription.

        Suggestions are candidates, not confirmed identities. Use IDs to distinguish
        same-named projects. Ask the user when candidates remain ambiguous.
        """
        return project_suggestions(query, self.service.db.list_projects())

    def list_devices(self) -> list[dict]:
        """List computers with conversation counts and identify the current computer."""
        return self.service.db.list_devices(stable_device_id())

    def get_project_history(self, project: str, limit: int = 20) -> list[dict]:
        return self.service.list_conversations(project_id=project, limit=limit)

    def get_sync_status(self) -> dict:
        return self.service.status()

    def sync_now(self) -> dict:
        from aimemory.state import now, write_json
        write_json(self.service.paths.state / "sync-request.json", {"requested_at": now()})
        return {"status": "queued", "message": "Le watcher traitera la demande. Consultez get_sync_status.",
                "watcher_running": bool((self.service.watcher_status() or {}).get("running"))}
