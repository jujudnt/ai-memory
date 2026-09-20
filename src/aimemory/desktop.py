from __future__ import annotations

import json
import subprocess
import threading
import uuid
import webbrowser
import secrets
import sys
import time
import tomllib
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from filelock import FileLock, Timeout
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from aimemory.installer import (
    claude_desktop_available,
    claude_desktop_config_path,
    get_watcher_service_status,
    install_all_mcp_configs,
    install_watcher_service,
    mcp_json_server,
    resolve_aimemory_command,
    vscode_user_mcp_config_path,
)
from aimemory.service import MemoryService
from aimemory.cloud.google_drive import GoogleDriveProvider
from aimemory.cloud.providers import LOCAL_FOLDER_PROVIDERS, RCLONE_DIRECT_PROVIDERS, resolve_folder_root, validate_sync_root
from aimemory.cloud.rclone_provider import RcloneCloudProvider
from aimemory.cloud.destination import cloud_folder, remote_path
from aimemory.sync.cloud_sync import cancel_active_sync
from aimemory.state import read_json, write_json
from aimemory.health import watcher_health


ASSETS = Path(__file__).with_name("assets")
HTML = (ASSETS / "desktop.html").read_text(encoding="utf-8")


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
                raise ValueError("Une operation est deja en cours.")
            self.job = {"running": True, "message": f"{name} en cours..."}
        def work():
            try:
                result = operation()
                message = result.get("message") if isinstance(result, dict) else None
                self.job = {"running": False, "message": message or f"{name} : termine.", "result": result}
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
            status["health"] = watcher_health(status["watcher"])
            status["mcp_clients"] = mcp_client_status()
            status["mcp_configured"] = mcp_configured(status["mcp_clients"])
            status["menubar_available"] = sys.platform == "darwin"
            from aimemory.menubar import login_path
            status["menubar_login"] = sys.platform == "darwin" and login_path().exists()
            try:
                with FileLock(str(self.state.service.paths.state / "sync.lock"), timeout=0):
                    status["sync_active"] = False
            except Timeout:
                status["sync_active"] = True
            status["recent_conversations"] = self.state.service.list_conversations(limit=25)
            status["icloud_auth"] = RcloneCloudProvider.for_provider(
                self.state.service.paths, "icloud-online"
            ).pending_icloud_auth()
            self._send_json(status)
            return
        if self.path == "/api/identity":
            self._send_json({"app": "ai-memory"})
            return
        assets = {"/assets/desktop.css": "text/css", "/assets/desktop.js": "text/javascript", "/assets/lucide.min.js": "text/javascript"}
        if self.path in assets:
            payload = (ASSETS / self.path.rsplit("/", 1)[1]).read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", assets[self.path])
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
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
        if self.path == "/api/cloud-folder":
            folder = str(self.body.get("folder") or "")
            config = read_json(self.state.service.paths.state / "cloud.json")
            if not config:
                raise ValueError("Connectez d'abord un compte cloud.")
            online = config["provider"] in RCLONE_DIRECT_PROVIDERS or config["provider"] == "google-drive"
            if online:
                folder = cloud_folder(folder)
            else:
                root = Path(folder).expanduser().resolve()
                validate_sync_root(root, self.state.service.paths.home)
                folder = str(root)
            def change_folder():
                _pull_current_destination(self.state.service)
                with _cloud_config_lock(self.state.service):
                    updated = dict(config, root=folder, connection_id=uuid.uuid4().hex)
                    if online:
                        provider = (GoogleDriveProvider(self.state.service.paths)
                                    if config["provider"] == "google-drive" else
                                    RcloneCloudProvider.for_provider(self.state.service.paths, config["provider"]))
                        provider.run(["mkdir", remote_path(updated)])
                    else:
                        Path(folder).mkdir(parents=True, exist_ok=True)
                    write_json(self.state.service.paths.state / "cloud.json", updated)
                    write_json(self.state.service.paths.state / "sync-status.json", {"status": "connected"})
                return self.state.service.sync_now()
            self._send_json(self.state.start_job("Changement de dossier et synchronisation", change_folder))
            return
        if self.path == "/api/reset-icloud":
            if self.state.job.get("running"):
                raise ValueError("Attendez la fin de l'opération en cours avant de recommencer.")
            with _cloud_config_lock(self.state.service):
                RcloneCloudProvider.for_provider(self.state.service.paths, "icloud-online").reset_icloud()
            self.state.job = {}
            self._send_json({"message": "Identifiants et session iCloud effacés. Vous pouvez recommencer."})
            return
        if self.path == "/api/menubar-login":
            from aimemory.menubar import set_login_enabled
            enabled = self.body.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("Expected a boolean")
            set_login_enabled(enabled, self.state.service.paths.state)
            self._send_json({"message": "Lancement automatique de l'interface mis a jour."})
            return
        if self.path == "/api/connect-cloud":
            provider = self.body.get("provider")
            folder = self.body.get("folder", "")
            def connect():
                continuing_icloud = provider == "icloud-online" and bool(
                    self.body.get("icloud_2fa")
                    or self.body.get("icloud_resume")
                    or self.body.get("icloud_restart_after_terms")
                )
                if not continuing_icloud:
                    _pull_current_destination(self.state.service)
                if provider == "google-drive":
                    with _cloud_config_lock(self.state.service):
                        GoogleDriveProvider(self.state.service.paths).connect(self.body.get("oauth_client"))
                elif provider in RCLONE_DIRECT_PROVIDERS:
                    options = {
                        "apple_id": self.body.get("icloud_apple_id"),
                        "password": self.body.get("icloud_password"),
                        "two_factor_code": self.body.get("icloud_2fa"),
                        "resume_after_approval": self.body.get("icloud_resume"),
                        "restart_after_terms": self.body.get("icloud_restart_after_terms"),
                        "onedrive_type": self.body.get("onedrive_type"),
                    }
                    with _cloud_config_lock(self.state.service):
                        result = RcloneCloudProvider.for_provider(self.state.service.paths, provider).connect(options)
                    if result and result.get("status") in {
                        "needs_2fa",
                        "needs_web_approval",
                        "needs_terms_acceptance",
                    }:
                        return result
                elif provider in LOCAL_FOLDER_PROVIDERS:
                    root = resolve_folder_root(provider, folder)
                    validate_sync_root(root, self.state.service.paths.home)
                    root.mkdir(parents=True, exist_ok=True)
                    with _cloud_config_lock(self.state.service):
                        write_json(
                            self.state.service.paths.state / "cloud.json",
                            {
                                "provider": provider,
                                "root": str(root),
                                "connection_id": uuid.uuid4().hex,
                            },
                        )
                else:
                    raise ValueError("Unknown provider")
                return self.state.service.sync_now()
            self._send_json(self.state.start_job("Connexion et synchronisation", connect))
            return
        if self.path == "/api/disconnect-cloud":
            with _cloud_config_lock(self.state.service):
                config = read_json(self.state.service.paths.state / "cloud.json")
                if config.get("provider") == "google-drive":
                    GoogleDriveProvider(self.state.service.paths).disconnect()
                elif config.get("provider") in RCLONE_DIRECT_PROVIDERS:
                    RcloneCloudProvider.for_provider(self.state.service.paths, config["provider"]).disconnect()
                else:
                    (self.state.service.paths.state / "cloud.json").unlink(missing_ok=True)
                write_json(self.state.service.paths.state / "sync-status.json", {"status": "disconnected"})
            self._send_json({"message": "Deconnecte. Les conversations locales et la sauvegarde distante sont conservees."})
            return
        if self.path == "/api/sync":
            self._send_json(self.state.start_job("Synchronisation", self.state.service.sync_now))
            return
        if self.path == "/api/audit":
            def audit():
                imported = self.state.service.import_all()
                report = self.state.service.audit_codex()
                if imported.errors or report["issues"] or report["missing_thread_ids"]:
                    raise ValueError("Verification found issues. See verification status and audit.json.")
                return report
            self._send_json(self.state.start_job("Verification des copies", audit))
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
            self._send_json({"message": "Dossier des conversations ouvert."})
            return
        if self.path == "/api/import":
            def import_now():
                result = self.state.service.import_all()
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
            installed = get_watcher_service_status()
            message = "Collecte automatique active." if installed.running else "Service installe, en attente de demarrage." if installed.installed else result.message
            self._send_json({"message": message, "result": asdict(result)})
            return
        if self.path == "/api/install-mcp":
            result = install_all_mcp_configs()
            self._send_json({"message": "MCP configure. Relancez Codex et Claude Desktop pour charger la connexion.", "result": asdict(result)})
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


def mcp_configured(clients: dict | None = None) -> bool:
    clients = clients or mcp_client_status()
    available = [client for client in clients.values() if client.get("available")]
    return bool(available) and all(client.get("configured") for client in available)


def mcp_client_status() -> dict:
    vscode_path = vscode_user_mcp_config_path()
    return {
        "codex": {
            "label": "Codex",
            "available": True,
            "configured": codex_mcp_configured(),
        },
        "claudeDesktop": {
            "label": "Claude Desktop",
            "available": claude_desktop_available(),
            "configured": claude_desktop_mcp_configured(),
        },
        "vscode": {
            "label": "VS Code",
            "available": vscode_path is not None,
            "configured": vscode_mcp_configured(vscode_path),
        },
    }


def codex_mcp_configured() -> bool:
    try:
        config = tomllib.loads(Path("~/.codex/config.toml").expanduser().read_text(encoding="utf-8"))
        server = config.get("mcp_servers", {}).get("ai-memory", {})
        return bool(server.get("command")) and server.get("enabled", True)
    except (OSError, ValueError):
        return False


def claude_desktop_mcp_configured() -> bool:
    try:
        config = json.loads(claude_desktop_config_path().read_text(encoding="utf-8"))
        server = config.get("mcpServers", {}).get("ai-memory", {})
        expected = mcp_json_server()
        return (
            bool(server.get("command"))
            and server.get("command") == expected.get("command")
            and server.get("args", []) == expected.get("args", [])
        )
    except (OSError, ValueError, TypeError):
        return False


def vscode_mcp_configured(config_path: Path | None = None) -> bool:
    try:
        path = config_path or vscode_user_mcp_config_path()
        if not path:
            return False
        config = json.loads(path.read_text(encoding="utf-8"))
        server = config.get("servers", {}).get("ai-memory", {})
        expected = mcp_json_server()
        return (
            bool(server.get("command"))
            and server.get("command") == expected.get("command")
            and server.get("args", []) == expected.get("args", [])
        )
    except (OSError, ValueError, TypeError):
        return False


@contextmanager
def _cloud_config_lock(service: MemoryService):
    lock = FileLock(str(service.paths.state / "sync.lock"), timeout=1)
    try:
        lock.acquire()
    except Timeout:
        cancel_active_sync(service.paths)
        lock = FileLock(str(service.paths.state / "sync.lock"), timeout=20)
        try:
            lock.acquire()
        except Timeout:
            raise ValueError("La synchronisation est encore en cours. Réessayez dans quelques secondes.") from None
    try:
        yield
    finally:
        lock.release()


def _pull_current_destination(service: MemoryService) -> dict:
    if not read_json(service.paths.state / "cloud.json"):
        return {"status": "not-configured"}
    try:
        return service.pull_cloud_now()
    except Timeout:
        cancel_active_sync(service.paths)
        lock = FileLock(str(service.paths.state / "sync.lock"), timeout=20)
        try:
            with lock:
                pass
        except Timeout:
            raise ValueError(
                "La synchronisation actuelle ne s'arrête pas. Réessayez dans quelques secondes."
            ) from None
        return service.pull_cloud_now()


def main(background: bool = False) -> int:
    background = background or "--background" in sys.argv
    Handler.state = DesktopState()
    state_path = Handler.state.service.paths.state
    lock = FileLock(str(state_path / "desktop.lock"), timeout=0)
    try:
        lock.acquire()
    except Timeout:
        if not background:
            open_existing(state_path)
        return 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}"
    write_json(state_path / "desktop.json", {"port": server.server_port})
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    if not background:
        webbrowser.open(url)
    print(f"AI Memory desktop is running at {url}", flush=True)
    try:
        if sys.platform == "darwin":
            from aimemory.menubar import ensure_login, run_menubar
            ensure_login(state_path)
            run_menubar(Handler.state.service, url)
        else:
            worker.join()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        (state_path / "desktop.json").unlink(missing_ok=True)
        lock.release()
    return 0


def open_existing(state_path: Path) -> None:
    from urllib.request import urlopen
    for _ in range(20):
        try:
            port = int(read_json(state_path / "desktop.json")["port"])
            url = f"http://127.0.0.1:{port}"
            with urlopen(url + "/api/identity", timeout=1) as response:
                if json.load(response).get("app") == "ai-memory":
                    webbrowser.open(url)
                    return
        except (OSError, ValueError, KeyError):
            time.sleep(0.1)


if __name__ == "__main__":
    raise SystemExit(main())
