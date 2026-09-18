# MCP Setup

AI Memory exposes a local stdio MCP server through the `aimemory-mcp` console script.

Install the package with MCP support:

```bash
python -m pip install "ai-memory[mcp]"
```

For local development from this repository:

```bash
python -m pip install -e ".[dev]"
```

Print a Codex-ready setup snippet:

```bash
aimemory mcp-config
```

Then either add it through the Codex CLI:

```bash
codex mcp add ai-memory -- aimemory-mcp
```

or add this to `~/.codex/config.toml`:

```toml
[mcp_servers.ai-memory]
command = "aimemory-mcp"
startup_timeout_sec = 10
tool_timeout_sec = 60
default_tools_approval_mode = "auto"
```

Codex, the ChatGPT desktop app, and the Codex IDE extension share the same MCP configuration on a Codex host. In Codex, use `/mcp` to verify that `ai-memory` is connected.

The server currently exposes:

- `search_conversations`
- `get_conversation`
- `list_conversations`
- `get_project_history`
- `get_sync_status`
- `sync_now`

## Watcher Status

The current collector is a polling watcher:

```bash
aimemory watch --interval 10
```

It writes status to:

```text
~/.ai-memory/state/watcher-status.json
```

The desktop app should read the same status file to show:

- running or stopped
- last scan time
- last successful import time
- sessions scanned/imported/skipped
- last error, if any
