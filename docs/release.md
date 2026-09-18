# Release Builds

AI Memory ships a first desktop executable through GitHub Releases.

The release artifact is intentionally simple:

- double-click opens the desktop UI
- `AI Memory watch` runs the local watcher
- `AI Memory mcp-server` runs the local stdio MCP server for Codex

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
- view watcher status and recent conversations

This is a functional MVP, not the final polished onboarding app.
