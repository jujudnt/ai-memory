# MCP Setup

AI Memory exposes one local stdio MCP server that can be used by Codex, Claude Desktop, VS Code, and other MCP clients.

## Release App

If you installed AI Memory from GitHub Releases, use the UI:

1. Open AI Memory.
2. Open **Reglages** with the slider icon.
3. Click **Activer** on **MCP pour Codex et Claude**.
4. Restart Codex, Claude Desktop, and VS Code if you use them.

The app first copies its standalone CLI helper to a stable user location:

- macOS/Linux: `~/.ai-memory/bin/ai-memory-cli`
- Windows: `%LOCALAPPDATA%\AI Memory\bin\ai-memory-cli.exe`

Every MCP client points to that copy instead of `AI Memory.app` or the downloaded executable. The desktop application can therefore be installed, moved, or launched from any folder without invalidating MCP. Enabling the collector uses the same stable runtime for its background watcher.

The app installs only the clients it can detect on the current computer:

- Codex: `~/.codex/config.toml`
- Claude Desktop: `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS
- VS Code: the user profile `mcp.json`

VS Code also supports MCP discovery from Claude Desktop, but AI Memory writes a direct VS Code user configuration so it works even when discovery is disabled.

Codex, Claude Desktop, and VS Code do not all reload MCP configuration live. Restart the client after enabling AI Memory.

## Development Install

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

Write AI Memory into the MCP clients available on the machine:

```bash
aimemory install-mcp
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

If you install AI Memory from a GitHub Release executable, the GUI writes similar configs but points each client back to the downloaded executable with the `mcp-server` argument.

In Codex, use `/mcp` to verify that `ai-memory` is connected. In VS Code, use **MCP: List Servers**. In Claude Desktop, restart the app and check that AI Memory tools are available in the tools/connectors list.

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
