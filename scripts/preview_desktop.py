"""Disposable UI fixture server. Never opens accounts, files or installed services."""
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

from aimemory.desktop import Handler
from aimemory.state import now


STATUS = {
    "version": "0.4.21-preview", "archive_count": 158, "conversation_count": 158, "project_count": 43,
    "storage": {"cloud_sync": "configured", "provider": "icloud-online", "provider_label": "iCloud Drive",
                "remote": "Sauvegardes/AI-Memory", "archive": "/example/AI-Memory/archive", "archive_bytes": 1800000000,
                "normalized_bytes": 178000000, "raw_bytes": 1200000000, "revisions_bytes": 422000000,
                "database_bytes": 900000000, "cache_bytes": 160000000, "total_bytes": 2860000000},
    "health": {"state": "error", "label": "Collecte en erreur"},
    "watcher": {"running": True, "last_error": "Erreur de collecte simulée", "last_success_at": now()},
    "watcher_service": {"installed": True}, "sync_active": True,
    "sync": {"status": "downloading", "transfers": 42, "totalTransfers": 180, "heartbeat_at": now()},
    "job": {"running": True}, "menubar_available": True, "menubar_login": True,
    "mcp_clients": {"claudeCode": {"available": True, "configured": True, "label": "Claude Code"}},
    "mcp_configured": True, "recent_conversations": [{"title": "Conversation de test", "source": "vscode-claude",
                                                    "latest_user_message": "Vérifier les boutons de connexion et la reprise des transferts.", "updated_at": now()}],
}


class PreviewHandler(Handler):
    state = SimpleNamespace(token="preview-only")

    def do_GET(self):
        if self.path == "/api/status":
            self._send_json(STATUS)
        else:
            super().do_GET()

    def _post(self):
        if self.path == "/api/pause-sync":
            STATUS.update(sync_active=False, sync_paused=True, job={})
            STATUS["sync"]["status"] = "paused"
        self._send_json({"message": "Simulation uniquement : aucune opération réelle."})


if __name__ == "__main__":
    with ThreadingHTTPServer(("127.0.0.1", 0), PreviewHandler) as server:
        print(f"Preview: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
