# AI Memory

Portable memory for AI coding assistants.

AI Memory backs up Codex conversations, synchronizes them to Google Drive or a local cloud folder such as iCloud Drive, OneDrive or Dropbox, indexes them locally, and exposes their history through MCP. Download the desktop application from [GitHub Releases](https://github.com/jujudnt/ai-memory/releases).

This repository intentionally starts with the data-safe core:

- Codex session discovery from `~/.codex/sessions` and `~/.codex/archived_sessions`
- A source-adapter boundary so future Claude Code, Cursor, and ChatGPT adapters do not leak into the normalized model
- Stable normalized JSON archives under `archive/sources/<source>/sessions`
- Local SQLite metadata plus FTS5 search under `db/memory.sqlite`
- Google Drive browser authorization through the bundled rclone helper
- iCloud Drive, OneDrive, Dropbox and custom synchronized-folder destinations
- Automatic bidirectional synchronization of immutable archives, with checksum verification
- Full compressed source backups, preserved conversation revisions, and an import integrity audit
- Local-folder synchronization, also usable with a folder managed by iCloud, OneDrive or Dropbox
- A real stdio MCP server entry point through `aimemory-mcp`
- A polling watcher with a status file that a desktop UI can display
- A compact local dashboard with settings for Drive, MCP and diagnostics
- A native macOS menu-bar icon with watcher health, last scan, login startup and single-instance protection
- Archive size, index size, total local storage, cloud transfer progress and last successful synchronization

No hosted backend, paid embedding API, or proprietary vector service is required.

## Quick Start

For the release application:

1. Extract the zip. On macOS, move `AI Memory.app` to Applications before installing the watcher or enabling MCP.
2. Open the app and click **Activer** beside the collector. Historical sessions are imported automatically; the collector continues after the UI closes.
3. Click **Connecter**, then choose **Google Drive**, **iCloud Drive**, **OneDrive**, **Dropbox** or **Autre dossier synchronise**. Google Drive opens a browser authorization under the name **rclone** and creates the `AI-Memory` folder. The other providers use the official local sync folder already installed on the Mac.
4. Open **Reglages** (the settings icon), activate **MCP pour Codex**, and restart the Codex client so it reads its updated configuration.
5. Under **Verification et diagnostics**, use **Verifier les copies** to compare original Codex files with raw backups and normalized messages/tool calls. Active sessions can change during the check and are reported separately.

On macOS, the layered memory icon stays in the menu bar when the browser closes. Click it to see the last successful scan or reopen the dashboard. A filled icon means collection is healthy; an outlined icon means inactive/starting; a warning triangle means a collection error, a cloud error, or no successful collection for more than two minutes (a long import can also trigger this warning). The menu-bar interface starts at login by default, independently of the watcher service. Disable its login switch in settings to opt out. **Quitter l'interface** closes the icon and local dashboard, not the installed watcher. Opening the app again reuses the existing instance instead of creating duplicate icons. The menu-bar feature is macOS-only; Windows keeps the browser dashboard.

On a second computer, connect the same Google account and let synchronization finish. Its local search index is built from the downloaded archives. Neither SQLite nor Google credentials are uploaded. Synchronization does not delete cloud history or restore conversations into Codex's native sidebar.

Cloud transfers run after connection, with the synchronization icon, or every minute while the watcher is running. The first full backup can be large. The UI reports bytes transferred, speed, last activity, errors, and last success. Interrupted copies resume by skipping immutable objects already present.

If Google Drive is full, AI Memory stops the transfer with an explicit quota message. Free space in Google Drive, disconnect and choose another destination, or use iCloud Drive/OneDrive/Dropbox if those clients have available storage. Folder-based providers check local free space before copying; their official client then handles the cloud upload.

Cloud archives are **not end-to-end encrypted** in this version. Google tokens are stored in the local `credentials` directory, restricted to the local user on macOS/Linux. Disconnect removes the local authorization and keeps both local and remote backups; revoke rclone access in your Google account to revoke the grant itself.

**Google OAuth:** rclone's shared Google client is being retired during 2026. The default flow is available for testing while it remains active. For durable use, create a Desktop OAuth client following [rclone's official instructions](https://rclone.org/drive/#making-your-own-client-id), then select its JSON under **Client OAuth personnel** before connecting. Use the same OAuth client on all computers. A different OAuth client cannot see files created under the old `drive.file` grant, so keep local backups when migrating.

For development:

From this directory:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python scripts/fetch_rclone.py
aimemory doctor
aimemory import-codex
aimemory search "Cloud Run memory"
aimemory mcp-config
aimemory desktop
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

This release implements the Codex + Google Drive/folder-cloud + local MCP flow. It is not the entire V1 specification:

1. Parse Codex sessions.
2. Normalize them into a source-independent schema.
3. Archive the normalized record.
4. Ingest metadata and message text into SQLite FTS5.
5. Search locally.
6. Expose reusable service methods for CLI, GUI, and MCP.
7. Run a local stdio MCP server for Codex.
8. Track watcher health for the future desktop app.
9. Package a first desktop executable through GitHub Releases.

Still pending: semantic/vector search, filesystem event collection instead of polling, direct OAuth for non-Google providers, private GitHub storage, optional end-to-end encryption, and Apple Developer ID signing/notarization.

### Storage layout

- `~/.ai-memory/archive/sources`: current normalized conversations.
- `~/.ai-memory/archive/raw`: compressed, byte-verifiable original Codex JSONL snapshots. Growing sessions store an initial full snapshot followed by compressed append deltas; source rewrites create a new full snapshot. `read_raw` reconstructs and checks the exact original bytes.
- `~/.ai-memory/archive/snapshots`: immutable normalized revisions, including divergent versions.
- `~/.ai-memory/db/memory.sqlite`: local full-text search index.
- `~/.ai-memory/cache/exchange`: transfer cache; included in total local storage.
- `~/.ai-memory/state/audit.json`: last verification report.

The latest conversation revision is indexed by timestamp and item count. Older and divergent revisions remain preserved in the archive; automatic semantic conflict merging is not implemented. Source conversation identifiers take precedence over inherited parent session identifiers, so forks remain separate.

See [docs/mcp.md](docs/mcp.md) for MCP setup.
