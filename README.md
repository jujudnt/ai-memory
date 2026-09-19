# AI Memory

Portable memory for AI coding assistants.

AI Memory backs up Codex and Claude conversations, synchronizes them to a cloud account or a local cloud folder, indexes them locally, and exposes their history through MCP. Download the desktop application from [GitHub Releases](https://github.com/jujudnt/ai-memory/releases).

This repository intentionally starts with the data-safe core:

- Codex session discovery from `~/.codex/sessions` and `~/.codex/archived_sessions`
- Claude Code, Claude Desktop coding-session and Claude-in-VS-Code discovery from `~/.claude/projects`
- Separate labels for Codex/Claude sessions launched from VS Code, and Claude Desktop coding sessions, when local metadata exposes them
- VS Code chat-session discovery when non-empty chat history exists under Code user storage
- Stable normalized JSON archives under `archive/sources/<source>/sessions`
- Local SQLite metadata plus FTS5 search under `db/memory.sqlite`
- Google Drive, Dropbox, OneDrive and iCloud Drive cloud connections through the bundled rclone helper
- iCloud Drive, OneDrive, Dropbox and custom synchronized-folder destinations for local-client workflows
- Automatic bidirectional synchronization of immutable archives, with checksum verification and cloud-confirmed local retention
- Full compressed source backups, preserved conversation revisions, and an import integrity audit
- Local-folder synchronization, also usable with a folder managed by iCloud, OneDrive or Dropbox
- A real stdio MCP server entry point for Codex, Claude Desktop and VS Code
- A polling watcher with a status file that a desktop UI can display
- A compact local dashboard with settings for Drive, MCP and diagnostics
- A native macOS menu-bar icon with watcher health, last scan, login startup and single-instance protection
- Conversation size, index size, total local storage, automatic cleanup after verified cloud backup, cloud transfer progress and last successful synchronization

No hosted backend, paid embedding API, or proprietary vector service is required.

## Quick Start

For the release application:

1. Extract the zip. On macOS, move `AI Memory.app` to Applications before installing the watcher or enabling MCP.
2. Open the app and click **Activer** beside the collector. Historical sessions are imported automatically; the collector continues after the UI closes.
3. Click **Connecter**, then choose **Google Drive**, **Dropbox**, **OneDrive**, **iCloud Drive** or a local-folder option. Google Drive, Dropbox and OneDrive open a browser authorization under the name **rclone** and create the `AI-Memory` folder. iCloud Drive online uses rclone credentials, separate from the iCloud account signed in to macOS; with Apple double authentication, validate the prompt on your Apple device, enter the 2FA code in AI Memory, and reconnect. The “du Mac” options use the official local sync folder already installed on the Mac.
4. Open **Reglages** (the settings icon), activate **MCP pour Codex et Claude**, then restart Codex, Claude Desktop and VS Code if you use them. The button installs AI Memory into the MCP clients detected on this computer.
5. Under **Verification et diagnostics**, use **Verifier les copies** to compare original Codex files with raw backups and normalized messages/tool calls. Active sessions can change during the check and are reported separately.

On macOS, the layered memory icon stays in the menu bar when the browser closes. Click it to see the last successful scan or reopen the dashboard. A filled icon means collection is healthy; an outlined icon means inactive/starting; a warning triangle means a collection error, a cloud error, or no successful collection for more than two minutes (a long import can also trigger this warning). The menu-bar interface starts at login by default, independently of the watcher service. Disable its login switch in settings to opt out. **Quitter l'interface** closes the icon and local dashboard, not the installed watcher. Opening the app again reuses the existing instance instead of creating duplicate icons. The menu-bar feature is macOS-only; Windows keeps the browser dashboard.

On a second computer, connect the same Google account and let synchronization finish. Its local search index is built from the downloaded archives. Neither SQLite nor Google credentials are uploaded. Synchronization does not delete cloud history or restore conversations into Codex's native sidebar.

Cloud transfers run after connection, with the synchronization icon, or every minute while the watcher is running. The first full backup can be large. The UI reports transferred objects, last activity, errors, last success, and how much local space was reclaimed. Interrupted copies resume by skipping immutable objects already present.

After a successful cloud transfer, AI Memory automatically removes the verified local copies of compressed originals and old immutable revisions. It keeps the current normalized conversations and SQLite search index on the computer so MCP search remains fast and works offline. The watcher remembers cloud-confirmed objects, so unchanged conversations are not reimported merely because their heavy local backup was cleaned. Changing cloud destination resets that confirmation for the new account and sends the current conversation set again.

If a cloud destination is full, AI Memory stops the transfer with an explicit quota message. Free space in that account or use **Changer de destination** to connect another cloud account without deleting local backups. Folder-based providers check local free space before copying; their official client then handles the cloud upload.

Cloud archives are **not end-to-end encrypted** in this version. rclone credentials are stored in the local `credentials` directory, restricted to the local user on macOS/Linux. Disconnect removes the local authorization, keeps the searchable current conversations on the computer, and does not delete the remote archive; revoke rclone access in the provider account to revoke the grant itself.

**iCloud Drive online:** use the normal Apple ID password. AI Memory first asks Apple to start the login; after Apple shows a 2FA code on a trusted device, AI Memory reveals the code field and the button becomes **Confirmer le code iCloud**. App-specific passwords are not accepted by rclone's iCloud Drive backend. If you want the iCloud account already connected to this Mac, choose **iCloud Drive du Mac** instead.

**Google OAuth:** rclone's shared Google client is being retired during 2026. The default flow is available for testing while it remains active. For durable use, create a Desktop OAuth client following [rclone's official instructions](https://rclone.org/drive/#making-your-own-client-id), then select its JSON under **Client OAuth personnel** before connecting. Use the same OAuth client on all computers. A different OAuth client cannot see files created under the old `drive.file` grant, so keep local backups when migrating.

For development:

From this directory:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python scripts/fetch_rclone.py
aimemory doctor
aimemory import-all
aimemory search "Cloud Run memory"
aimemory mcp-config
aimemory install-mcp
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

This release implements the Codex/Claude + rclone cloud/folder-cloud + local MCP flow. It is not the entire V1 specification:

1. Parse Codex, Claude Code, Claude Desktop coding sessions, Claude-in-VS-Code sessions, plus non-empty VS Code chat sessions.
2. Normalize them into a source-independent schema.
3. Archive the normalized record.
4. Ingest metadata and message text into SQLite FTS5.
5. Search locally.
6. Expose reusable service methods for CLI, GUI, and MCP.
7. Run a local stdio MCP server for Codex, Claude Desktop and VS Code.
8. Track watcher health for the future desktop app.
9. Package a first desktop executable through GitHub Releases.

Still pending: semantic/vector search, filesystem event collection instead of polling, Cursor-specific adapters, Claude web/Cowork/Design conversation imports when a stable local export or API is available, private GitHub storage, optional end-to-end encryption, and Apple Developer ID signing/notarization.

### Storage layout

- `~/.ai-memory/archive/sources`: current normalized conversations.
- `~/.ai-memory/archive/raw`: compressed, byte-verifiable original JSONL snapshots waiting for cloud confirmation. After successful synchronization, these heavy local copies are removed while the verified objects remain in the configured cloud archive.
- `~/.ai-memory/archive/snapshots`: immutable normalized revisions waiting for cloud confirmation, including divergent versions. Verified revisions are also cleaned locally after synchronization.
- `~/.ai-memory/db/memory.sqlite`: local full-text search index.
- `~/.ai-memory/cache/incoming`: temporary download verification area, deleted after every synchronization attempt.
- `~/.ai-memory/state/audit.json`: last verification report.

The latest conversation revision is indexed by timestamp and item count. Older and divergent revisions remain preserved in the archive; automatic semantic conflict merging is not implemented. Source conversation identifiers take precedence over inherited parent session identifiers, so forks remain separate.

## MCP Setup

The release app includes the MCP server. To install it:

1. Open AI Memory.
2. Open **Reglages** with the slider icon.
3. Click **Activer** on **MCP pour Codex et Claude**.
4. Restart the clients you use: Codex, Claude Desktop and VS Code.
5. In Codex, run `/mcp`; in VS Code, open **MCP: List Servers**; in Claude Desktop, check the connector/tools list after restart.

The button writes the same local server into the MCP configs it can find:

- Codex: `~/.codex/config.toml`
- Claude Desktop: `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS
- VS Code: the user `mcp.json` profile file

If you do not use the desktop UI, run `aimemory install-mcp` from a terminal. See [docs/mcp.md](docs/mcp.md) for the exact config formats.
