from __future__ import annotations

import json
import socket
import subprocess
import threading
import webbrowser
import secrets
from dataclasses import asdict
from pathlib import Path
from filelock import FileLock
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from aimemory.installer import (
    get_watcher_service_status,
    install_mcp_config,
    install_watcher_service,
    resolve_aimemory_command,
)
from aimemory.service import MemoryService
from aimemory.cloud.google_drive import GoogleDriveProvider
from aimemory.state import read_json, write_json


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
    [hidden] { display: none !important; }
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
      border-top: 1px solid var(--line);
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
    p, code { overflow-wrap: anywhere; }
    .storage-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; margin: 16px 0; }
    .storage-grid strong { display: block; font-size: 24px; }
    .actions { display: flex; gap: 8px; flex-wrap: wrap; }
    input, select { font: inherit; padding: 8px; border: 1px solid var(--line); border-radius: 4px; max-width: 100%; }
    label { display: block; margin: 8px 0; }
    .progress { color: var(--accent-strong); font-weight: 600; }
    @media (max-width: 760px) {
      .toolbar, .stats { grid-template-columns: 1fr; }
      .storage-grid { grid-template-columns: 1fr; }
      th:nth-child(1), td:nth-child(1), th:nth-child(3), td:nth-child(3) { width: auto; }
    }
  </style>
</head>
<body>
  <header>
    <h1>AI Memory</h1>
    <div class="muted">Codex conversations · v0.2.3</div>
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
      <div class="storage-grid">
        <div><strong id="archive-size">—</strong>Conversation folder</div>
        <div><strong id="db-size">—</strong>Local search index</div>
        <div><strong id="total-size">—</strong>Total on this computer</div>
      </div>
      <p><code id="archive-path"></code></p>
      <p id="storage-detail" class="muted"></p>
      <div class="actions"><button class="secondary" data-action="open-folder">Open conversation folder</button></div>
    </section>
    <section class="panel">
      <h2>Cloud synchronization</h2>
      <label for="provider">Storage provider</label>
      <select id="provider"><option value="google-drive">Google Drive</option><option value="local-folder">Local or cloud-synced folder</option></select>
      <label id="folder-label" hidden>Folder path <input id="folder" placeholder="/path/to/AI-Memory" size="50"></label>
      <p id="provider-note" class="muted">Google authorization opens in your browser under the name rclone. Compressed conversations and full source backups are stored in AI-Memory. No end-to-end encryption in this version.</p>
      <details id="oauth-settings"><summary>Google OAuth settings</summary>
        <p>The shared rclone Google authorization is being retired during 2026. For a lasting setup, use your own Desktop OAuth client on every computer. Changing OAuth clients may require uploading the local archive again.</p>
        <label>Desktop OAuth client JSON <input id="oauth-client" type="file" accept="application/json,.json"></label>
        <p><a href="https://rclone.org/drive/#making-your-own-client-id" target="_blank" rel="noreferrer">Google OAuth setup</a></p>
      </details>
      <div class="actions">
        <button data-action="connect-cloud">Connect Google Drive</button>
        <button data-action="sync">Sync now</button>
        <button class="secondary" data-action="disconnect-cloud">Disconnect</button>
      </div>
      <p id="cloud-detail" class="progress" aria-live="polite">Not connected</p>
      <p id="cloud-last" class="muted"></p>
    </section>
    <section class="panel">
      <h2>Import verification</h2>
      <div class="actions"><button class="secondary" data-action="audit">Verify conversations</button></div>
      <p id="audit-detail" aria-live="polite">Not verified yet</p>
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
    const token = '__API_TOKEN__';
    let jobRunning = false;
    function size(bytes) { const n = Number(bytes || 0); const units = ['B', 'KiB', 'MiB', 'GiB']; const i = Math.min(3, Math.floor(Math.log(Math.max(n, 1)) / Math.log(1024))); return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`; }
    function date(value) { return value ? new Date(value).toLocaleString() : '—'; }
    function escape(value) { const el = document.createElement('span'); el.textContent = text(value); return el.innerHTML; }
    function setBusy(busy) { buttons.forEach(button => button.disabled = busy); }
    function text(value) { return value == null || value === '' ? '—' : String(value); }
    async function refresh() {
      const res = await fetch('/api/status');
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || 'Unable to refresh');
      jobRunning = data.job && data.job.running;
      setBusy(jobRunning);
      if (data.job && data.job.message) message.textContent = data.job.message;
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
      document.querySelector('#archive-size').textContent = size(storage.archive_bytes);
      document.querySelector('#db-size').textContent = size(storage.database_bytes);
      document.querySelector('#total-size').textContent = size(storage.total_bytes);
      document.querySelector('#archive-path').textContent = storage.archive;
      document.querySelector('#storage-detail').textContent = `Compressed conversations: ${size(storage.normalized_bytes)} · Full source backups: ${size(storage.raw_bytes)} · Preserved revisions: ${size(storage.revisions_bytes)}`;
      const sync = data.sync || {};
      document.querySelector('[data-action="sync"]').disabled = jobRunning || storage.cloud_sync !== 'configured';
      document.querySelector('[data-action="disconnect-cloud"]').disabled = jobRunning || storage.cloud_sync !== 'configured';
      document.querySelector('#cloud-detail').textContent = `${storage.cloud_sync === 'configured' ? storage.provider + ' / ' + storage.remote : 'Not connected'} · ${sync.status || 'idle'}${sync.error ? ': ' + sync.error : ''}`;
      document.querySelector('#cloud-last').textContent = `Last successful sync: ${date(sync.last_success_at)} · Objects verified: ${sync.object_count || 0} · Transferred: ${size(sync.bytes)} / ${size(sync.totalBytes)} · ${size(sync.speed)}/s · Last activity: ${date(sync.heartbeat_at)}`;
      const audit = data.audit || {};
      document.querySelector('#audit-detail').textContent = audit.checked_at ? `${audit.verified_files}/${audit.files} source backups verified · ${audit.unique_sessions} distinct conversations · ${(audit.issues || []).length} errors · ${(audit.changing_files || []).length} changed since import · ${(audit.missing_thread_ids || []).length} Codex threads without source files · ${date(audit.checked_at)}` : 'Not verified yet';
      document.querySelector('#watcher-detail').textContent = watcher
        ? `Installed: ${service && service.installed ? 'yes' : 'no'} | Last success: ${text(watcher.last_success_at)} | Last scan: ${text(watcher.last_scan_at)} | Scanned/imported/skipped: ${watcher.scanned || 0}/${watcher.imported || 0}/${watcher.skipped || 0}${watcher.last_error ? ' | Error: ' + watcher.last_error : ''}`
        : `Installed: ${service && service.installed ? 'yes' : 'no'} | No watcher import status yet.`;
      const rows = data.recent_conversations || [];
      document.querySelector('#conversations').innerHTML = rows.map(row => `
        <tr>
          <td>${escape(date(row.updated_at || row.created_at))}</td>
          <td>${escape(row.title || row.source_session_id || row.id)}</td>
          <td>${escape(row.source)}</td>
        </tr>
      `).join('');
    }
    async function action(name) {
      if (name === 'refresh') return refresh();
      setBusy(true);
      message.textContent = `${name} started...`;
      try {
        const file = document.querySelector('#oauth-client').files[0];
        const oauthClient = name === 'connect-cloud' && file ? JSON.parse(await file.text()) : null;
        const res = await fetch(`/api/${name}`, { method: 'POST', headers: {'Content-Type': 'application/json', 'X-AI-Memory-Token': token}, body: JSON.stringify({provider: document.querySelector('#provider').value, folder: document.querySelector('#folder').value, oauth_client: oauthClient}) });
        const data = await res.json();
        if (!res.ok) throw new Error(data.message);
        message.textContent = data.message || JSON.stringify(data);
        await refresh();
      } catch (error) {
        message.textContent = String(error);
      } finally {
        setBusy(jobRunning);
      }
    }
    buttons.forEach(button => button.addEventListener('click', () => action(button.dataset.action)));
    document.querySelector('#provider').addEventListener('change', event => {
      const google = event.target.value === 'google-drive';
      document.querySelector('#folder-label').hidden = google;
      document.querySelector('#oauth-settings').hidden = !google;
      document.querySelector('[data-action="connect-cloud"]').textContent = google ? 'Connect Google Drive' : 'Connect folder';
      document.querySelector('#provider-note').textContent = google ? 'Google authorization opens in your browser under the name rclone. Compressed conversations and full source backups are stored in AI-Memory. No end-to-end encryption in this version.' : 'Choose a folder already synchronized by iCloud, OneDrive or Dropbox, or a local folder. No end-to-end encryption in this version.';
    });
    refresh().catch(error => message.textContent = error.message);
    setInterval(() => refresh().catch(error => message.textContent = error.message), 5000);
  </script>
</body>
</html>
"""


class DesktopState:
    def __init__(self):
        self.service = MemoryService()
        self.watcher_process: subprocess.Popen | None = None
        self.token = secrets.token_urlsafe(32)
        self.job = {}
        self.job_lock = threading.Lock()

    def start_job(self, name, operation):
        with self.job_lock:
            if self.job.get("running"):
                raise ValueError("Another operation is already running.")
            self.job = {"running": True, "message": f"{name} in progress..."}
        def work():
            try:
                result = operation()
                self.job = {"running": False, "message": f"{name} complete.", "result": result}
            except Exception as exc:
                self.job = {"running": False, "message": str(exc), "error": True}
        threading.Thread(target=work, daemon=True).start()
        return {"message": self.job["message"]}


class Handler(BaseHTTPRequestHandler):
    state: DesktopState

    def do_GET(self) -> None:
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if self.path == "/" or self.path.startswith("/?"):
            self._send_html(HTML.replace("__API_TOKEN__", self.state.token))
            return
        if self.path == "/api/status":
            status = self.state.service.status()
            status["watcher_service"] = asdict(get_watcher_service_status())
            status["job"] = self.state.job
            status["recent_conversations"] = self.state.service.list_conversations(limit=25)
            self._send_json(status)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.headers.get("X-AI-Memory-Token") != self.state.token or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 8192:
                raise ValueError("Request too large")
            self.body = json.loads(self.rfile.read(length) or b"{}")
            self._post()
        except Exception as exc:
            self._send_json({"message": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _post(self) -> None:
        if self.path == "/api/connect-cloud":
            provider = self.body.get("provider")
            folder = self.body.get("folder", "")
            def connect():
                if provider == "google-drive":
                    with FileLock(str(self.state.service.paths.state / "sync.lock"), timeout=1):
                        GoogleDriveProvider(self.state.service.paths).connect(self.body.get("oauth_client"))
                elif provider == "local-folder":
                    if not folder.strip():
                        raise ValueError("Choose a folder first")
                    root = Path(folder).expanduser().resolve()
                    home = self.state.service.paths.home.resolve()
                    if root == home or home in root.parents or root in home.parents:
                        raise ValueError("Choose a folder outside AI Memory's data folder")
                    root.mkdir(parents=True, exist_ok=True)
                    with FileLock(str(self.state.service.paths.state / "sync.lock"), timeout=1):
                        write_json(self.state.service.paths.state / "cloud.json", {"provider": provider, "root": str(root)})
                else:
                    raise ValueError("Unknown provider")
                return self.state.service.sync_now()
            self._send_json(self.state.start_job("Cloud connection and synchronization", connect))
            return
        if self.path == "/api/disconnect-cloud":
            with FileLock(str(self.state.service.paths.state / "sync.lock"), timeout=1):
                GoogleDriveProvider(self.state.service.paths).disconnect()
                write_json(self.state.service.paths.state / "sync-status.json", {"status": "disconnected"})
            self._send_json({"message": "Disconnected. Local and remote backups have been kept."})
            return
        if self.path == "/api/sync":
            self._send_json(self.state.start_job("Synchronization", self.state.service.sync_now))
            return
        if self.path == "/api/audit":
            def audit():
                imported = self.state.service.import_codex()
                report = self.state.service.audit_codex()
                if imported.errors or report["issues"] or report["missing_thread_ids"]:
                    raise ValueError("Verification found issues. See verification status and audit.json.")
                return report
            self._send_json(self.state.start_job("Import verification", audit))
            return
        if self.path == "/api/open-folder":
            import sys, os
            path = str(self.state.service.paths.archive)
            if sys.platform == "darwin":
                subprocess.Popen(["open", path])
            elif os.name == "nt":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path])
            self._send_json({"message": "Conversation folder opened."})
            return
        if self.path == "/api/import":
            def import_now():
                result = self.state.service.import_codex()
                if result.errors:
                    raise ValueError(f"{len(result.errors)} import failures: {result.errors[0]['error']}")
                return asdict(result)
            self._send_json(self.state.start_job("Import", import_now))
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
                    "watcher_service": asdict(service_status),
                })
                return
            command = [*resolve_aimemory_command(), "watch", "--interval", "10"]
            self.state.watcher_process = subprocess.Popen(command)
            self._send_json({"message": "Watcher started for this session.", "command": command})
            return
        if self.path == "/api/install-watcher":
            result = install_watcher_service()
            self._send_json({"message": result.message, "result": asdict(result)})
            return
        if self.path == "/api/install-mcp":
            result = install_mcp_config()
            self._send_json({"message": result.message, "result": asdict(result)})
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

    def _send_json(self, data: dict, status=HTTPStatus.OK) -> None:
        payload = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
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
