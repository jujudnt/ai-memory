# AI Memory

Portable memory for AI coding assistants.

AI Memory is an open-source, local-first memory layer for AI coding conversations. The first implementation slice supports Codex JSONL session import, normalized conversation archives, SQLite full-text search, project grouping, and a CLI that exercises the same core service future GUI and MCP transports can use.

This repository intentionally starts with the data-safe core:

- Codex session discovery from `~/.codex/sessions` and `~/.codex/archived_sessions`
- A source-adapter boundary so future Claude Code, Cursor, and ChatGPT adapters do not leak into the normalized model
- Stable normalized JSON archives under `archive/sources/<source>/sessions`
- Local SQLite metadata plus FTS5 search under `db/memory.sqlite`
- Local-folder sync primitives for the first provider abstraction
- A real stdio MCP server entry point through `aimemory-mcp`
- A polling watcher with a status file that a desktop UI can display

No hosted backend, paid embedding API, or proprietary vector service is required.

## Quick Start

From this directory:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
aimemory doctor
aimemory import-codex
aimemory search "Cloud Run memory"
aimemory mcp-config
```

Without installing the console script, use:

```bash
PYTHONPATH=src python -m aimemory.cli doctor
```

By default, local data is stored in `~/.ai-memory`. Override it with:

```bash
export AI_MEMORY_HOME=/path/to/local/state
```

## Current Scope

This is not yet the full desktop app described in the product specification. It is the first durable layer:

1. Parse Codex sessions.
2. Normalize them into a source-independent schema.
3. Archive the normalized record.
4. Ingest metadata and message text into SQLite FTS5.
5. Search locally.
6. Expose reusable service methods for CLI, GUI, and MCP.
7. Run a local stdio MCP server for Codex.
8. Track watcher health for the future desktop app.

The vector index, cloud OAuth providers, native background service installers, tray app, and encryption are next layers on top of this core.

See [docs/mcp.md](docs/mcp.md) for MCP setup.
