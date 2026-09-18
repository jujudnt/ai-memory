from __future__ import annotations

from aimemory.mcp.tools import MemoryToolHandlers

INSTRUCTIONS = (
    "AI Memory exposes local, user-owned AI coding conversation history. "
    "Use search_conversations for targeted retrieval, get_project_history for "
    "continuity on the current project, and get_conversation only when a full "
    "session is needed. Prefer concise summaries over dumping raw history."
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
    server.tool()(handlers.list_conversations)
    server.tool()(handlers.get_project_history)
    server.tool()(handlers.get_sync_status)
    server.tool()(handlers.sync_now)
    return server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
