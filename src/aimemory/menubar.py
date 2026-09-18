"""Native macOS status item. Imported only by the desktop process on macOS."""
from __future__ import annotations

import plistlib
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from aimemory.cloud.providers import provider_label
from aimemory.health import watcher_health
from aimemory.state import read_json, write_json

LOGIN_LABEL = "io.github.jujudnt.ai-memory.menubar"


def login_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LOGIN_LABEL}.plist"


def set_login_enabled(enabled: bool, state_path: Path) -> None:
    if sys.platform != "darwin":
        raise ValueError("Menu bar startup is available only on macOS.")
    path = login_path()
    if enabled:
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--background"]
        else:
            command = [sys.executable, "-m", "aimemory.desktop", "--background"]
        path.parent.mkdir(parents=True, exist_ok=True)
        logs = state_path.parent / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps({
            "Label": LOGIN_LABEL, "ProgramArguments": command,
            "RunAtLoad": True, "ProcessType": "Interactive",
            "EnvironmentVariables": {"AI_MEMORY_HOME": str(state_path.parent)},
            "StandardOutPath": str(logs / "menubar.out.log"),
            "StandardErrorPath": str(logs / "menubar.err.log"),
        }))
    else:
        path.unlink(missing_ok=True)
    write_json(state_path / "desktop-preferences.json", {"login_enabled": enabled})


def ensure_login(state_path: Path) -> None:
    # Development previews must never replace the installed application's login entry.
    if getattr(sys, "frozen", False):
        prefs = read_json(state_path / "desktop-preferences.json")
        if prefs.get("login_enabled", True):
            set_login_enabled(True, state_path)


def run_menubar(service, url: str) -> None:
    import AppKit
    from Foundation import NSObject, NSTimer, NSRunLoop, NSRunLoopCommonModes
    from PyObjCTools import AppHelper

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    class Delegate(NSObject):
        def openDashboard_(self, sender):
            webbrowser.open(url)

        def openFolder_(self, sender):
            AppKit.NSWorkspace.sharedWorkspace().openFile_(str(service.paths.archive))

        def quitMenu_(self, sender):
            AppHelper.stopEventLoop()

        def applicationShouldHandleReopen_hasVisibleWindows_(self, application, visible):
            webbrowser.open(url)
            return False

        def tick_(self, timer):
            try:
                watcher = service.watcher_status() or {}
                health = watcher_health(watcher)
                sync = read_json(service.paths.state / "sync-status.json")
                cloud = read_json(service.paths.state / "cloud.json")
                state_item.setTitle_(health["label"])
                stamp = watcher.get("last_success_at")
                last = datetime.fromisoformat(stamp).astimezone().strftime("%H:%M:%S") if stamp else "en attente"
                scan_item.setTitle_(f"Dernier scan : {last}")
                cloud_label = "Cloud non connect\u00e9"
                if cloud:
                    provider = provider_label(cloud.get("provider"))
                    phase = {"synced": "\u00e0 jour", "error": "erreur", "preparing": "pr\u00e9paration", "verifying": "v\u00e9rification"}.get(sync.get("status"), "synchronisation")
                    cloud_label = f"{provider} : {phase}"
                cloud_item.setTitle_(cloud_label)
                warning = health["state"] in {"error", "stale"} or (cloud and sync.get("status") == "error")
                symbol = "exclamationmark.triangle" if warning else "square.stack.3d.up.fill" if health["state"] == "running" else "square.stack.3d.up"
                button = status_item.button()
                button.setImage_(images[symbol])
                button.setToolTip_(f"AI Memory - {health['label']}\nDernier scan : {last}\n{cloud_label}")
                button.setAccessibilityLabel_(f"AI Memory - {health['label']}")
            except Exception:
                state_item.setTitle_("\u00c9tat indisponible")
                status_item.button().setImage_(images["exclamationmark.triangle"])
                status_item.button().setToolTip_("AI Memory - Etat indisponible")

    delegate = Delegate.alloc().init()
    app.setDelegate_(delegate)
    status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)
    menu = AppKit.NSMenu.alloc().init()
    images = {}
    for symbol in ["square.stack.3d.up.fill", "square.stack.3d.up", "exclamationmark.triangle"]:
        icon = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, "AI Memory")
        icon.setSize_((18, 18))
        icon.setTemplate_(True)
        images[symbol] = icon

    def item(title, selector=None):
        value = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, selector, "")
        if selector:
            value.setTarget_(delegate)
        menu.addItem_(value)
        return value

    item("AI Memory")
    state_item = item("Lecture de l'etat...")
    scan_item = item("Dernier scan : en attente")
    cloud_item = item("Cloud")
    menu.addItem_(AppKit.NSMenuItem.separatorItem())
    item("Ouvrir AI Memory", "openDashboard:")
    item("Dossier des conversations", "openFolder:")
    menu.addItem_(AppKit.NSMenuItem.separatorItem())
    item("Le watcher reste actif sans cette interface")
    item("Quitter l'interface", "quitMenu:")
    status_item.setMenu_(menu)
    delegate.tick_(None)
    timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(5, delegate, "tick:", None, True)
    NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
    try:
        AppHelper.runEventLoop()
    finally:
        timer.invalidate()
        AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(status_item)
