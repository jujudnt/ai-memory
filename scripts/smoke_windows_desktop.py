"""Run the packaged Windows GUI and watch for unwanted console windows."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
from urllib.request import Request, urlopen


def windows() -> dict[int, tuple[str, bool]]:
    result = {}
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]

    @callback_type
    def visit(hwnd, _):
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, name, len(name))
        result[int(hwnd)] = (name.value, bool(user32.IsWindowVisible(hwnd)))
        return True

    if not user32.EnumWindows(visit, 0):
        raise ctypes.WinError()
    return result


def main() -> None:
    assert sys.platform == "win32", "This test requires real Windows."
    executable = str(Path(sys.argv[1]).resolve())
    baseline = set(windows())
    consoles = set()
    failures = []
    stop = threading.Event()

    def monitor():
        try:
            while not stop.is_set():
                consoles.update(hwnd for hwnd, (name, visible) in windows().items()
                                if hwnd not in baseline and name == "ConsoleWindowClass" and visible)
                stop.wait(0.005)
        except Exception as exc:
            failures.append(str(exc))

    observer = threading.Thread(target=monitor, daemon=True)
    observer.start()
    try:
        # Calibrate the detector before testing the GUI: a deliberately visible
        # console must be observed, otherwise the runner cannot verify flashing.
        subprocess.run([sys.executable, "-c", "import time; time.sleep(1)"],
                       creationflags=subprocess.CREATE_NEW_CONSOLE, check=True, timeout=15)
        assert consoles, "Runner cannot observe console windows; visual check unavailable."
        deadline = time.monotonic() + 5
        while any(hwnd in consoles for hwnd in windows()):
            assert time.monotonic() < deadline, "Calibration console did not close"
            time.sleep(0.05)
        time.sleep(0.05)
        consoles.clear()

        with TemporaryDirectory(prefix="ai-memory-windows-gui-") as directory:
            root = Path(directory)
            state = root / "memory" / "state"
            state.mkdir(parents=True)
            (state / "desktop-preferences.json").write_text('{"login_enabled": false}')
            env = {**os.environ, "HOME": directory, "USERPROFILE": directory,
                   "APPDATA": str(root / "appdata"), "LOCALAPPDATA": str(root / "localappdata"),
                   "AI_MEMORY_HOME": str(root / "memory"), "CODEX_HOME": str(root / "codex"),
                   "CLAUDE_CONFIG_DIR": str(root / "claude")}
            source = root / "codex" / "sessions" / "smoke.jsonl"
            source.parent.mkdir(parents=True)
            source.write_text('\n'.join(json.dumps(row) for row in [
                {"type": "session_meta", "payload": {"id": "windows-gui-smoke"}},
                {"type": "response_item", "timestamp": "2026-09-22T00:00:00Z", "payload": {
                    "type": "message", "role": "user", "content": "Windows GUI smoke marker"}},
            ]) + '\n')
            process = subprocess.Popen([executable, "--background"], env=env)
            try:
                deadline = time.monotonic() + 120
                while not (state / "desktop.json").exists():
                    assert process.poll() is None, f"GUI exited: {process.returncode}"
                    assert time.monotonic() < deadline, "GUI startup timed out"
                    time.sleep(0.2)
                port = json.loads((state / "desktop.json").read_text())["port"]
                base = f"http://127.0.0.1:{port}"

                def request(path, token=None):
                    req = Request(base + path, data=b"{}" if token else None,
                                  headers={"X-AI-Memory-Token": token} if token else {})
                    with urlopen(req, timeout=30) as response:
                        return response.read().decode()

                assert json.loads(request("/api/identity"))["app"] == "ai-memory"
                token = re.search(r'data-token="([^"]+)"', request("/")).group(1)
                for path in ("/assets/desktop.js", "/assets/desktop.css", "/assets/lucide.min.js"):
                    assert len(request(path)) > 100
                request("/api/start-watcher", token)
                for _ in range(12):
                    status = json.loads(request("/api/status"))
                    assert status["status_icon_platform"] == "windows"
                    assert status["menubar_available"] is True
                    assert process.poll() is None
                    time.sleep(1)
                assert status["conversation_count"] == 1, status
                assert status["watcher"]["last_error"] is None, status
                assert any(name.startswith("ai-memory") and name.endswith("SystemTrayIcon")
                           for name, _ in windows().values()), "Tray window not registered"
                assert not failures, failures
                assert not consoles, f"Unwanted console windows observed: {consoles}"
                print("Windows GUI: tray window, assets, 12 status polls, hidden watcher and import passed; no console windows observed.")
            finally:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True, timeout=30)
                process.wait(timeout=30)
    finally:
        stop.set()
        observer.join(timeout=5)


if __name__ == "__main__":
    main()
