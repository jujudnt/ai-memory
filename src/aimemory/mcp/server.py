from __future__ import annotations

from aimemory.mcp.tools import MemoryToolHandlers

INSTRUCTIONS = (
    "AI Memory exposes local, user-owned AI coding conversation history. "
    "When the user asks to find a past conversation, use search_conversations first. "
    "Translate natural requests into project, source and device filters: 'Sabai on my "
    "other Mac' means project='Sabai', device='other', source='codex' if specified, query=''. "
    "Use list_projects(query=...) for uncertain spelling or speech transcription, "
    "and list_devices to disambiguate computers. Never claim an uncertain name match is certain. "
    "Read get_conversation(latest=True, limit=6) to confirm the latest exchanges. "
    "Results are bounded and indexed; get_message continues a truncated entry. "
    "Prefer these tools over reading raw files or dumping entire archives. "
    "Mention the latest received timestamp; local import completion does not prove cloud delivery."
)


def build_server():
    handlers = MemoryToolHandlers()
    try:
        from mcp.server import MCPServer

        server = MCPServer("ai-memory", instructions=INSTRUCTIONS)
    except Exception:  # pragma: no cover - compatibility with older SDKs
        try:
            from mcp.server.fastmcp import FastMCP

            server = FastMCP("ai-memory", instructions=INSTRUCTIONS)
        except Exception as exc:
            raise SystemExit("Install ai-memory[mcp] to run the MCP server") from exc

    server.tool()(handlers.search_conversations)
    server.tool()(handlers.get_conversation)
    server.tool()(handlers.get_message)
    server.tool()(handlers.list_devices)
    server.tool()(handlers.list_conversations)
    server.tool()(handlers.get_project_history)
    server.tool()(handlers.list_projects)
    server.tool()(handlers.get_sync_status)
    server.tool()(handlers.sync_now)
    return server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
