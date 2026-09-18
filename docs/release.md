# Release Builds

AI Memory ships a first desktop app through GitHub Releases.

The release artifact is intentionally simple:

- double-click opens the desktop UI
- macOS packages `AI Memory.app` with an internal `ai-memory-cli` helper
- `AI Memory watch` runs the local watcher on Windows and dev installs
- `AI Memory mcp-server` runs the local stdio MCP server for Codex on Windows and dev installs

Create a release by pushing a tag:

```bash
git tag v0.1.0
git push origin v0.1.0
```

GitHub Actions builds:

- `ai-memory-macos.zip`
- `ai-memory-windows.zip`

The release executable opens a local browser UI. It currently supports:

- import now
- start watcher for the current session
- install watcher at login
- enable MCP in `~/.codex/config.toml`
- show local storage location
- view watcher status and recent conversations

V0.1 stores data locally only, under `~/.ai-memory`, until cloud providers are added.

This is a functional MVP, not the final polished onboarding app.

## macOS Gatekeeper

The macOS build is ad-hoc signed, but it is not notarized with an Apple Developer ID yet. macOS may still show an unidentified developer or malware-verification warning after download.

For this MVP, open it with right-click > Open, or remove quarantine manually:

```bash
xattr -dr com.apple.quarantine "/path/to/AI Memory.app"
```

Removing this warning completely requires a paid Apple Developer account, Developer ID signing certificate, and Apple notarization in the release workflow.
