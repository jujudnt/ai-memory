"""Exercise the actual packaged helper without touching the user's installation."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


async def check_mcp(executable: str, env: dict) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async with stdio_client(StdioServerParameters(command=executable, args=["mcp-server"], env=env)) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            result = await session.list_tools()
            assert {"search_conversations", "list_projects", "get_sync_status"} <= {t.name for t in result.tools}
            response = await session.call_tool("search_conversations", {"query": "packaged-smoke-marker"})
            assert not getattr(response, "is_error", getattr(response, "isError", False)), response
            assert "packaged-smoke-marker" in str(response.content)


def main() -> None:
    executable = str(Path(sys.argv[1]).resolve())
    with TemporaryDirectory(prefix="ai-memory-packaged-test-") as directory:
        root = Path(directory)
        env = {**os.environ, "HOME": directory, "USERPROFILE": directory,
               "APPDATA": str(root / "appdata"), "XDG_CONFIG_HOME": str(root / "config"),
               "AI_MEMORY_HOME": str(root / "memory"), "CODEX_HOME": str(root / "codex"),
               "CLAUDE_CONFIG_DIR": str(root / "claude")}
        source = root / "codex" / "sessions" / "smoke.jsonl"
        source.parent.mkdir(parents=True)
        source.write_text("\n".join(json.dumps(r) for r in [
            {"type": "session_meta", "payload": {"id": "smoke"}},
            {"type": "response_item", "timestamp": "2026-09-21T00:00:00Z", "payload": {
                "type": "message", "role": "user", "content": "packaged-smoke-marker"}},
        ]) + "\n")
        def run(*args):
            return subprocess.run([executable, *args], env=env, capture_output=True,
                                  text=True, timeout=60, check=True).stdout
        run("--help")
        run("watch", "--once")
        status = json.loads(run("doctor"))
        assert status["conversation_count"] == 1, status
        assert status["watcher"]["last_error"] is None, status
        asyncio.run(asyncio.wait_for(check_mcp(executable, env), timeout=60))
    print("Packaged CLI, isolated watcher and MCP handshake/search passed.")


if __name__ == "__main__":
    main()
