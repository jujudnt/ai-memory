# AI Memory Roadmap

## Implemented in this MVP

- Codex JSONL discovery and parsing
- Source-independent normalized conversation schema
- Project identity from normalized Git remotes, with path fallback
- Compressed local archives
- SQLite metadata and FTS5 search
- CLI for doctor, import, search, get, rebuild, local-folder sync, MCP config, and watcher status
- Transport-neutral MCP tool handlers
- Real stdio MCP entry point through `aimemory-mcp`
- Polling watcher with a status file for desktop UI integration

## Next Phase

1. Add deterministic chunking around user/assistant/tool groups.
2. Add local embeddings and LanceDB behind an optional semantic-search interface.
3. Replace polling with the watchdog-based background collector and startup service installers.
4. Build the first desktop UI around setup, MCP enablement, and watcher status.
5. Add rclone-backed providers for Google Drive, iCloud Drive, OneDrive, and Dropbox.
6. Add GitHub private repository provider with visibility checks.
7. Add optional encrypted archives using established cryptographic libraries and OS-native key storage.
8. Build the desktop onboarding flow on top of the same `MemoryService`.
