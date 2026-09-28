"""Windows notification-area icon for the local desktop process."""
from __future__ import annotations

import os
import threading
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

    images = {
        "running": _tray_image(Image, ImageDraw, "#ffffff"),
        "idle": _tray_image(Image, ImageDraw, "#9a9e93"),
        "warning": _tray_image(Image, ImageDraw, "#ffffff", alert=True),
    }
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


LAYERS = (  # The app glyph, in its 24-unit design grid.
    ((12, 2.6), (20.6, 7.3), (12, 12), (3.4, 7.3), (12, 2.6)),
    ((3.4, 11.9), (12, 16.6), (20.6, 11.9)),
    ((3.4, 16.1), (12, 20.8), (20.6, 16.1)),
)


def _tray_image(Image, ImageDraw, color: str, alert: bool = False):
    """App badge (dark rounded square, light layers) that remains legible at 16 px."""
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((5, 5, 59, 59), radius=13, fill="#1a1c1a")
    scale, offset, width = 1.5, 14, 4
    for path in LAYERS:
        points = [(offset + x * scale, offset + y * scale) for x, y in path]
        draw.line(points, fill=color, width=width, joint="curve")
        for x, y in (points[0], points[-1]):
            draw.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=color)
    if alert:
        draw.ellipse((40, 40, 58, 58), fill="#d84a3a", outline="#1a1c1a", width=3)
    return image
