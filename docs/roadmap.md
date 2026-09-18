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
- First local-browser desktop UI
- GitHub Actions CI and release builds for macOS and Windows
- Google Drive browser OAuth using bundled, checksum-verified rclone
- Immutable bidirectional cloud archive synchronization and automatic local reindexing
- Raw Codex backups and source-to-archive integrity verification
- Correct fork/session identity and custom tool-call preservation
- Visible archive/index/total storage sizes and cloud transfer activity
- Process-level locks for watcher singleton and shared data operations

## Next Phase

1. Add deterministic chunking around user/assistant/tool groups.
2. Add local embeddings and LanceDB behind an optional semantic-search interface.
3. Replace polling with the watchdog-based background collector.
4. Improve the desktop UI with provider setup, onboarding steps, and tray/menu-bar mode.
5. Add direct OAuth for iCloud Drive, OneDrive and Dropbox (their existing synchronized folders already work).
6. Add GitHub private repository provider with visibility checks.
7. Add optional encrypted archives using established cryptographic libraries and OS-native key storage.
8. Build the desktop onboarding flow on top of the same `MemoryService`.
