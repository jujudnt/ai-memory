"""Windows notification-area icon for the local desktop process."""
from __future__ import annotations

import os
import threading
import time
import webbrowser
from datetime import datetime

from aimemory.cloud.providers import provider_label
from aimemory.health import watcher_health
from aimemory.state import read_json


def run_windows_tray(service, url: str) -> None:
    import pystray
    from PIL import Image, ImageDraw

    state = {"health": "Chargement...", "scan": "Dernier scan : en attente", "cloud": "Cloud"}
    stopped = threading.Event()

    def open_dashboard(icon=None, item=None):
        webbrowser.open(url)

    def open_folder(icon=None, item=None):
        os.startfile(str(service.paths.archive))

    def quit_interface(icon, item=None):
        stopped.set()
        icon.stop()

    def status_menu():
        return pystray.Menu(
            pystray.MenuItem("AI Memory", None, enabled=False),
            pystray.MenuItem(lambda item: state["health"], None, enabled=False),
            pystray.MenuItem(lambda item: state["scan"], None, enabled=False),
            pystray.MenuItem(lambda item: state["cloud"], None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Ouvrir AI Memory", open_dashboard, default=True),
            pystray.MenuItem("Dossier des conversations", open_folder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Le watcher reste actif sans cette interface", None, enabled=False),
            pystray.MenuItem("Quitter l'interface", quit_interface),
        )

    images = {name: _tray_image(Image, ImageDraw, color) for name, color in {
        "running": "#16835b", "idle": "#59636e", "warning": "#d84a3a",
    }.items()}
    icon = pystray.Icon("ai-memory", images["idle"], "AI Memory", status_menu())

    def update() -> None:
        while not stopped.is_set():
            try:
                watcher = service.watcher_status() or {}
                health = watcher_health(watcher)
                sync = read_json(service.paths.state / "sync-status.json")
                cloud = read_json(service.paths.state / "cloud.json")
                stamp = watcher.get("last_success_at")
                last = datetime.fromisoformat(stamp).astimezone().strftime("%H:%M:%S") if stamp else "en attente"
                cloud_label = "Cloud non connecté"
                if cloud:
                    phase = {"synced": "à jour", "error": "erreur",
                             "waiting_local_cloud": "en attente du dossier local",
                             "preparing": "préparation", "verifying": "vérification"}.get(
                                 sync.get("status"), "synchronisation")
                    cloud_label = f"{provider_label(cloud.get('provider'))} : {phase}"
                warning = health["state"] in {"error", "stale"} or (cloud and sync.get("status") == "error")
                state.update(health=health["label"], scan=f"Dernier scan : {last}", cloud=cloud_label)
                icon.icon = images["warning" if warning else "running" if health["state"] == "running" else "idle"]
                icon.title = f"AI Memory - {health['label']}\nDernier scan : {last}\n{cloud_label}"
                icon.update_menu()
            except Exception:
                state["health"] = "État indisponible"
                icon.icon = images["warning"]
                icon.title = "AI Memory - État indisponible"
                icon.update_menu()
            stopped.wait(5)

    def setup(tray):
        tray.visible = True
        threading.Thread(target=update, daemon=True, name="ai-memory-tray-status").start()

    icon.run(setup=setup)


def _tray_image(Image, ImageDraw, color: str):
    """Small high-contrast stacked-memory mark that remains legible at 16 px."""
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((5, 5, 59, 59), radius=13, fill="#171a1c")
    for points in (
        ((17, 25), (32, 16), (47, 25), (32, 34)),
        ((17, 34), (32, 43), (47, 34)),
        ((17, 43), (32, 52), (47, 43)),
    ):
        draw.line(points, fill=color, width=5, joint="curve")
    return image
