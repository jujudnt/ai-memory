# AI Memory Roadmap

## Available Now

- Codex, Claude Code, Claude Desktop coding-session, Claude-in-VS-Code, and VS Code chat discovery
- Source-independent normalized conversation schema
- Project identity from source metadata, Git remotes, and working-directory fallback
- Compressed original backups and immutable normalized revisions
- SQLite metadata and FTS5 full-text search
- Local stdio MCP server for Codex, Claude Desktop, and VS Code
- Polling watcher with automatic login startup and status reporting
- Desktop dashboard and native macOS menu-bar companion
- Google Drive, Dropbox, OneDrive, and iCloud Drive online connections
- iCloud Drive, Dropbox, OneDrive, and custom synchronized-folder destinations
- Bidirectional synchronization, checksum verification, and local index rebuilds
- Cloud-confirmed cleanup of heavy local originals and historical revisions
- Destination migration, quota handling, transfer resumption, and integrity audits
- Signed and Apple-notarized macOS releases plus Windows release builds

## Planned

1. Add deterministic chunking around user, assistant, and tool-call groups.
2. Add optional local embeddings and vector search using an established storage engine.
3. Replace or complement polling with filesystem event collection.
4. Add a dedicated Cursor adapter when a stable local format is available.
5. Import Claude Web, Cowork, and Design conversations when a stable local export or supported API exists.
6. Add a private GitHub repository storage provider with visibility checks.
7. Add optional end-to-end encrypted archives using established cryptographic libraries and OS-native key storage.
8. Expand automated recovery and cross-platform end-to-end tests for every cloud provider.
