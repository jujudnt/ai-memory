# Release Builds

AI Memory ships a first desktop app through GitHub Releases.

The release artifact is intentionally simple:

- double-click opens the desktop UI
- macOS packages `AI Memory.app` with an internal `ai-memory-cli` helper
- `AI Memory watch` runs the local watcher on Windows and dev installs
- `AI Memory mcp-server` runs the local stdio MCP server for Codex on Windows and dev installs

Create a release by pushing a tag:

```bash
git tag v0.3.3
git push origin v0.3.3
```

GitHub Actions builds:

- `ai-memory-macos.zip`
- `ai-memory-windows.zip`

The release executable opens a local browser UI. It currently supports:

- a compact French dashboard with archive size and live watcher/cloud health
- one action to enable automatic collection, including historical sessions
- settings for Google Drive, MCP, login startup, import and integrity checks
- a native macOS status item that remains when the browser closes
- single-instance protection and background login startup without opening a browser

V0.2 adds Google Drive OAuth, automatic bidirectional synchronization, full raw backups, import verification and storage sizes. The helper is bundled in each release; no separate rclone installation or terminal configuration is required. `scripts/fetch_rclone.py` pins rclone v1.75.1 and checks its SHA-256 before packaging, and includes its MIT license.

V0.3 adds the simplified dashboard and native macOS menu-bar companion. macOS desktop dependencies include PyObjC Cocoa. Local HTML, CSS, JavaScript and MIT-licensed Lucide icons are bundled with `--collect-data aimemory`; there is no CDN request at runtime. `LSUIElement` keeps the companion out of the Dock. The watcher runs independently: quitting the menu interface does not stop collection. Login launch is managed by a separate `io.github.jujudnt.ai-memory.menubar` LaunchAgent and can be disabled in settings.

V0.3.2 adds iCloud Drive, OneDrive, Dropbox and custom synchronized-folder destinations through local provider folders, plus clearer Google Drive quota-full errors.

V0.3.3 lets the desktop UI change or disconnect the cloud destination while a transfer is running by cancelling the active transfer first, instead of surfacing a lock-file error.

V0.4.1 separates Codex and Claude sessions launched from VS Code in the local index, while preserving their canonical archive IDs. It also improves iCloud Drive online setup guidance for Apple double authentication.

V0.4.2 adds the missing iCloud Drive 2FA input field and passes rclone's `config_2fa` value during online iCloud setup. It also clarifies that iCloud Drive requires the normal Apple ID password plus 2FA, not an app-specific password.

V0.4.3 turns iCloud Drive online setup into a two-step Apple 2FA flow, installs the same local MCP server for detected Codex, Claude Desktop and VS Code clients, documents the release-app MCP setup path, labels Claude Desktop/VS Code sources separately, and maps Codex/Claude conversations back to local project folders when the source exposes them.

V0.4.4 fixes the real asynchronous Apple 2FA flow: once iCloud asks for validation, the open settings panel reveals the code field and changes the action to **Confirmer le code iCloud**. It also replaces the persistent full transfer cache with object-by-object synchronization, removes raw originals and historical revisions from the Mac only after their cloud writes succeed, preserves the local searchable conversation set, and resets cloud confirmation when the destination account changes.

V0.4.5 preserves rclone's pending Apple authentication session between the password and 2FA steps. Confirming the six-digit code now resumes that session instead of triggering a second Apple login notification. The credentials fields are hidden and cleared while confirmation is pending. Before switching accounts or providers, AI Memory also performs a download-only pass against the old destination, then uploads the consolidated current conversation set to the new destination; a full old account therefore does not block migration. Connector tests cover Google Drive, Dropbox, OneDrive, iCloud Drive, local cloud folders, and old-to-new destination migration.

## macOS Gatekeeper

The macOS build is ad-hoc signed, but it is not notarized with an Apple Developer ID yet. macOS may still show an unidentified developer or malware-verification warning after download.

For this MVP, open it with right-click > Open, or remove quarantine manually:

```bash
xattr -dr com.apple.quarantine "/path/to/AI Memory.app"
```

Removing this warning completely requires a paid Apple Developer account, Developer ID signing certificate, and Apple notarization in the release workflow.
