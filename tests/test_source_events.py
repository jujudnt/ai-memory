import sys
import threading
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aimemory.processes import lower_background_priority
from aimemory.watcher.events import SourceEvents
from aimemory.watcher.service import WatcherService
from aimemory.config import AppPaths


@pytest.mark.parametrize("priority,changed", [(0x20, True), (0x40, False), (0x4000, False)])
def test_windows_background_priority_never_increases(monkeypatch, priority, changed):
    nice = Mock(return_value=priority)
    psutil = SimpleNamespace(Process=lambda: SimpleNamespace(nice=nice), Error=RuntimeError,
                             IDLE_PRIORITY_CLASS=0x40, BELOW_NORMAL_PRIORITY_CLASS=0x4000)
    monkeypatch.setitem(sys.modules, "psutil", psutil)
    monkeypatch.setattr("aimemory.processes.sys", SimpleNamespace(platform="win32"))
    lower_background_priority()
    assert nice.call_count == (2 if changed else 1)
    if changed:
        assert nice.call_args.args == (0x4000,)


def test_windows_priority_failure_does_not_stop_collection(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=Mock(side_effect=OSError)))
    monkeypatch.setattr("aimemory.processes.sys", SimpleNamespace(platform="win32"))
    lower_background_priority()


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows priority")
def test_native_windows_priority():
    result = subprocess.run([sys.executable, "-c", "import psutil; from aimemory.processes import lower_background_priority; "
                             "lower_background_priority(); assert psutil.Process().nice() in "
                             "{psutil.IDLE_PRIORITY_CLASS, psutil.BELOW_NORMAL_PRIORITY_CLASS}"],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_read_events_and_own_status_do_not_trigger_collection(tmp_path):
    state = tmp_path / "state"
    events = SourceEvents(state, tmp_path / "codex")
    for name, kind, directory in [(str(state), "modified", True),
                                  (str(state / "watcher-status.json"), "modified", False),
                                  (str(tmp_path / "a.jsonl"), "opened", False)]:
        events.notify(SimpleNamespace(src_path=name, dest_path="", event_type=kind, is_directory=directory))
    assert not events.changed.is_set()
    events.notify(SimpleNamespace(src_path=str(tmp_path / "a.jsonl"), dest_path="", event_type="modified", is_directory=False))
    assert events.changed.is_set()
    events.notify(SimpleNamespace(src_path=str(state / "sync-request.json"), dest_path="", event_type="created", is_directory=False))
    assert events.requested.is_set()


def test_native_notification_detects_new_file_and_request(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr("aimemory.watcher.events.VSCodeAdapter", lambda: SimpleNamespace(discover=lambda: []))
    state, codex = tmp_path / "state", tmp_path / "codex"
    state.mkdir()
    sessions = codex / "sessions"
    sessions.mkdir(parents=True)
    events = SourceEvents(state, codex)
    assert events.start(), "Native file observer must be available on CI"
    try:
        (sessions / "test.jsonl").write_text("{}\n")
        assert events.changed.wait(5)
        (state / "sync-request.json").write_text("{}")
        assert events.requested.wait(5)
        assert events.alive
    finally:
        events.close()


@pytest.mark.parametrize("native", [True, False])
def test_idle_scans_and_polling_fallback(tmp_path, monkeypatch, native):
    monkeypatch.setenv("AI_MEMORY_HOME", str(tmp_path))
    paths = AppPaths.from_env()
    paths.ensure()
    clock = [0]
    scans = []
    class Events:
        alive = True
        changed = threading.Event()
        requested = SimpleNamespace(wait=lambda timeout: advance(timeout), clear=lambda: None)
        def __init__(self, *args): pass
        def start(self): return native
        def refresh(self): pass
        def close(self): pass
    def advance(seconds):
        clock[0] += seconds
        if clock[0] > 61:
            raise KeyboardInterrupt
    watcher = WatcherService(SimpleNamespace(paths=paths))
    monkeypatch.setattr("aimemory.watcher.service.SourceEvents", Events)
    monkeypatch.setattr("aimemory.watcher.service.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("aimemory.watcher.service.time.sleep", advance)
    monkeypatch.setattr("aimemory.watcher.service.lower_background_priority", lambda: None)
    monkeypatch.setattr(watcher, "scan_once", lambda *a: scans.append(clock[0]))
    monkeypatch.setattr(watcher, "_maybe_sync", lambda: None)
    with pytest.raises(KeyboardInterrupt):
        watcher.run_polling(interval_seconds=10)
    assert scans == ([0, 60] if native else [0, 10, 20, 30, 40, 50, 60])
