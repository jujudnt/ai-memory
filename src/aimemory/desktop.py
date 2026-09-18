from __future__ import annotations

import json
import socket
import subprocess
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from aimemory.installer import (
    get_watcher_service_status,
    install_mcp_config,
    install_watcher_service,
    resolve_aimemory_command,
)
from aimemory.service import MemoryService


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Memory</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #20242b;
      --muted: #667085;
      --line: #d8dee8;
      --accent: #176b87;
      --accent-strong: #0f536b;
      --ok: #267a43;
      --warn: #b35a00;
      --bad: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      padding: 18px 24px;
    }
    h1 {
      font-size: 24px;
      line-height: 1.2;
      margin: 0 0 4px;
      letter-spacing: 0;
    }
    h2 {
      font-size: 16px;
      margin: 0 0 10px;
    }
    main {
      width: min(1120px, calc(100vw - 32px));
      margin: 18px auto 32px;
      display: grid;
      gap: 16px;
    }
    .toolbar {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
    }
    button {
      min-height: 38px;
      border: 1px solid var(--accent-strong);
      background: var(--accent);
      color: white;
      border-radius: 6px;
      padding: 8px 10px;
      font: inherit;
      cursor: pointer;
    }
    button.secondary {
      background: white;
      color: var(--accent-strong);
      border-color: var(--line);
    }
    button:disabled { opacity: .65; cursor: wait; }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 12px;
    }
    .stat {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 12px;
      min-height: 78px;
    }
    .stat b {
      display: block;
      font-size: 22px;
      line-height: 1.1;
      margin-bottom: 4px;
    }
    .muted { color: var(--muted); }
    .ok { color: var(--ok); }
    .warn { color: var(--warn); }
    .bad { color: var(--bad); }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 10px 8px;
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }
    th {
      color: var(--muted);
      font-weight: 600;
      font-size: 12px;
      text-transform: uppercase;
    }
    th:nth-child(1), td:nth-child(1) { width: 190px; }
    th:nth-child(3), td:nth-child(3) { width: 90px; }
    #message {
      min-height: 22px;
      color: var(--muted);
    }
    @media (max-width: 760px) {
      .toolbar, .stats { grid-template-columns: 1fr; }
      th:nth-child(1), td:nth-child(1), th:nth-child(3), td:nth-child(3) { width: auto; }
    }
  </style>
</head>
<body>
  <header>
    <h1>AI Memory</h1>
    <div class="muted">Local Codex memory, watcher status, and MCP setup.</div>
  </header>
  <main>
    <section class="toolbar">
      <button data-action="import">Import Now</button>
      <button data-action="start-watcher">Start Watcher</button>
      <button data-action="install-watcher">Install Watcher</button>
      <button data-action="install-mcp">Enable MCP</button>
      <button class="secondary" data-action="refresh">Refresh</button>
    </section>
    <div id="message"></div>
    <section class="panel">
      <div class="stats">
        <div class="stat"><b id="conversation-count">0</b><span class="muted">Conversations</span></div>
        <div class="stat"><b id="archive-count">0</b><span class="muted">Archives</span></div>
        <div class="stat"><b id="project-count">0</b><span class="muted">Projects</span></div>
        <div class="stat"><b id="watcher-state">Unknown</b><span class="muted">Watcher</span></div>
        <div class="stat"><b id="storage-state">Local</b><span class="muted">Storage</span></div>
      </div>
    </section>
    <section class="panel">
      <h2>Storage</h2>
      <p id="storage-detail" class="muted">Local folder storage.</p>
    </section>
    <section class="panel">
      <h2>Watcher</h2>
      <p id="watcher-detail" class="muted">No watcher status yet.</p>
    </section>
    <section class="panel">
      <h2>Recent Conversations</h2>
      <table>
        <thead><tr><th>Updated</th><th>Conversation</th><th>Source</th></tr></thead>
        <tbody id="conversations"></tbody>
      </table>
    </section>
  </main>
  <script>
    const message = document.querySelector('#message');
    const buttons = [...document.querySelectorAll('button[data-action]')];
    function setBusy(busy) { buttons.forEach(button => button.disabled = busy); }
    function text(value) { return value == null || value === '' ? '—' : String(value); }
    async function refresh() {
      const res = await fetch('/api/status');
      const data = await res.json();
      document.querySelector('#conversation-count').textContent = data.conversation_count ?? 0;
      document.querySelector('#archive-count').textContent = data.archive_count ?? 0;
      document.querySelector('#project-count').textContent = data.project_count ?? 0;
      const watcher = data.watcher || null;
      const service = data.watcher_service || null;
      const isRunning = (watcher && watcher.running) || (service && service.running);
      const state = isRunning ? 'Running' : (service && service.installed ? 'Installed' : (watcher ? 'Stopped' : 'Not installed'));
      const stateEl = document.querySelector('#watcher-state');
      stateEl.textContent = state;
      stateEl.className = watcher && watcher.last_error ? 'bad' : (isRunning ? 'ok' : 'warn');
      const storage = data.storage || {};
      document.querySelector('#storage-state').textContent = storage.provider === 'local-folder' ? 'Local' : text(storage.provider);
      document.querySelector('#storage-detail').textContent = `Provider: ${text(storage.provider)} | Data: ${text(storage.home)} | Archive: ${text(storage.archive)} | Cloud sync: ${text(storage.cloud_sync)}`;
      document.querySelector('#watcher-detail').textContent = watcher
        ? `Installed: ${service && service.installed ? 'yes' : 'no'} | Last success: ${text(watcher.last_success_at)} | Last scan: ${text(watcher.last_scan_at)} | Scanned/imported/skipped: ${watcher.scanned || 0}/${watcher.imported || 0}/${watcher.skipped || 0}${watcher.last_error ? ' | Error: ' + watcher.last_error : ''}`
        : `Installed: ${service && service.installed ? 'yes' : 'no'} | No watcher import status yet.`;
      const rows = data.recent_conversations || [];
      document.querySelector('#conversations').innerHTML = rows.map(row => `
        <tr>
          <td>${text(row.updated_at || row.created_at)}</td>
          <td>${text(row.title || row.source_session_id || row.id)}</td>
          <td>${text(row.source)}</td>
        </tr>
      `).join('');
    }
    async function action(name) {
      if (name === 'refresh') return refresh();
      setBusy(true);
      message.textContent = `${name} started...`;
      try {
        const res = await fetch(`/api/${name}`, { method: 'POST' });
        const data = await res.json();
        message.textContent = data.message || JSON.stringify(data);
        await refresh();
      } catch (error) {
        message.textContent = String(error);
      } finally {
        setBusy(false);
      }
    }
    buttons.forEach(button => button.addEventListener('click', () => action(button.dataset.action)));
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""


class DesktopState:
    def __init__(self):
        self.service = MemoryService()
        self.watcher_process: subprocess.Popen | None = None


class Handler(BaseHTTPRequestHandler):
    state: DesktopState

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            self._send_html(HTML)
            return
        if self.path == "/api/status":
            status = self.state.service.status()
            status["watcher_service"] = get_watcher_service_status().__dict__
            status["recent_conversations"] = self.state.service.list_conversations(limit=25)
            self._send_json(status)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path == "/api/import":
            result = self.state.service.import_codex()
            self._send_json({"message": "Import complete.", "result": result.__dict__})
            return
        if self.path == "/api/start-watcher":
            if self.state.watcher_process and self.state.watcher_process.poll() is None:
                self._send_json({"message": "Watcher is already running from this app."})
                return
            service_status = get_watcher_service_status()
            watcher_status = self.state.service.watcher_status() or {}
            if service_status.running or watcher_status.get("running"):
                self._send_json({
                    "message": "Watcher is already running.",
                    "watcher_service": service_status.__dict__,
                })
                return
            command = [*resolve_aimemory_command(), "watch", "--interval", "10"]
            self.state.watcher_process = subprocess.Popen(command)
            self._send_json({"message": "Watcher started for this session.", "command": command})
            return
        if self.path == "/api/install-watcher":
            result = install_watcher_service()
            self._send_json({"message": result.message, "result": result.__dict__})
            return
        if self.path == "/api/install-mcp":
            result = install_mcp_config()
            self._send_json({"message": result.message, "result": result.__dict__})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args) -> None:
        return

    def _send_html(self, html: str) -> None:
        payload = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> int:
    port = _find_port()
    Handler.state = DesktopState()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    print(f"AI Memory desktop is running at {url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _find_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


if __name__ == "__main__":
    raise SystemExit(main())
