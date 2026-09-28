import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time
from types import SimpleNamespace
import uuid
import xml.etree.ElementTree as ET

import pytest
from filelock import FileLock

from aimemory import installer
from aimemory.adapters.vscode import VSCodeAdapter, _workspace_folder


def test_scheduler_status_uses_process_lock_on_french_windows(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MEMORY_HOME", str(tmp_path))
    (tmp_path / "state").mkdir()
    monkeypatch.setattr(installer.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout="Statut : En cours", stderr=""))
    assert installer._windows_task_status().installed
    assert not installer._windows_task_status().running
    with FileLock(str(tmp_path / "state" / "watcher.lock")):
        assert installer._windows_task_status().running
    assert not installer._windows_task_status().running


def test_task_definition_is_hidden_scoped_and_continuous(tmp_path):
    launcher = tmp_path / "Julia & equipe" / "watcher-launch.vbs"
    root = ET.fromstring(installer._windows_task_xml(launcher, "S-1-5-21-1-2-3-1000"))
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.findtext("t:Triggers/t:LogonTrigger/t:UserId", namespaces=ns) == "S-1-5-21-1-2-3-1000"
    assert root.findtext("t:Principals/t:Principal/t:LogonType", namespaces=ns) == "InteractiveToken"
    assert root.findtext("t:Principals/t:Principal/t:RunLevel", namespaces=ns) == "LeastPrivilege"
    assert root.findtext("t:Settings/t:ExecutionTimeLimit", namespaces=ns) == "PT0S"
    assert root.findtext("t:Settings/t:DisallowStartIfOnBatteries", namespaces=ns) == "false"
    assert root.findtext("t:Settings/t:StopIfGoingOnBatteries", namespaces=ns) == "false"
    assert root.findtext("t:Actions/t:Exec/t:Command", namespaces=ns).endswith("wscript.exe")
    assert str(launcher) in root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns)


@pytest.mark.parametrize("mismatch", ["command", "home", None])
def test_restart_only_stops_verified_watcher(tmp_path, monkeypatch, mismatch):
    monkeypatch.setenv("AI_MEMORY_HOME", str(tmp_path))
    paths = installer.AppPaths.from_env()
    paths.ensure()
    (paths.state / "watcher-status.json").write_text(json.dumps({"pid": 123}))
    lock = FileLock(str(paths.state / "watcher.lock"))
    lock.acquire()
    stopped = []
    def terminate():
        stopped.append(123)
        lock.release()
    process = SimpleNamespace(
        cmdline=lambda: ["helper.exe", "mcp-server" if mismatch == "command" else "watch"],
        environ=lambda: {"AI_MEMORY_HOME": str(tmp_path / "other" if mismatch == "home" else tmp_path)},
        children=lambda recursive: [], terminate=terminate,
    )
    class NoSuchProcess(Exception):
        pass
    class AccessDenied(Exception):
        pass
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(
        Process=lambda pid: process, NoSuchProcess=NoSuchProcess, AccessDenied=AccessDenied,
        wait_procs=lambda processes, timeout: (processes, [])))
    try:
        if mismatch:
            with pytest.raises(RuntimeError, match="ne correspond pas"):
                installer._stop_windows_watcher(paths, ["helper.exe", "watch", "--interval", "60"])
            assert not stopped
        else:
            installer._stop_windows_watcher(paths, ["helper.exe", "watch", "--interval", "60"])
            assert stopped == [123]
    finally:
        lock.release()


def test_running_runtime_can_be_replaced_and_old_copy_cleaned(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    source, destination = tmp_path / "new.exe", tmp_path / "installed.exe"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    os.utime(source, (1700000000, 1700000000))
    os.utime(destination, (1700000000, 1700000000))
    replace = os.replace
    unlink = Path.unlink
    def locked_replace(src, dst):
        if Path(dst) == destination and destination.exists():
            raise PermissionError("running image")
        return replace(src, dst)
    def locked_unlink(path, *a, **kw):
        if path.name.endswith(".previous"):
            raise PermissionError("still running")
        return unlink(path, *a, **kw)
    monkeypatch.setattr(installer.os, "replace", locked_replace)
    monkeypatch.setattr(Path, "unlink", locked_unlink)
    assert installer._copy_runtime_file(source, destination)
    assert destination.read_bytes() == b"new"
    previous = list(tmp_path.glob("*.previous"))
    assert len(previous) == 1 and previous[0].read_bytes() == b"old"
    monkeypatch.setattr(Path, "unlink", unlink)
    assert not installer._copy_runtime_file(source, destination)
    assert not list(tmp_path.glob("*.previous"))


def test_failed_runtime_update_restores_previous_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    source, destination = tmp_path / "new.exe", tmp_path / "installed.exe"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    replace = os.replace
    def fail_install(src, dst):
        if Path(src).name.endswith(".tmp"):
            raise PermissionError("installation denied")
        return replace(src, dst)
    monkeypatch.setattr(installer.os, "replace", fail_install)
    with pytest.raises(PermissionError):
        installer._copy_runtime_file(source, destination)
    assert destination.read_bytes() == b"old"
    assert not list(tmp_path.glob("*.tmp"))


def test_vscode_discovery_respects_redirected_windows_appdata(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "redirected"))
    session = tmp_path / "redirected/Code - Insiders/User/workspaceStorage/project/chatSessions/chat.json"
    session.parent.mkdir(parents=True)
    session.write_text("{}")
    assert session in [item.path for item in VSCodeAdapter().scan_sessions()]


@pytest.mark.parametrize("uri,expected", [
    ("file:///C%3A/Users/Julia/Mon%20projet", r"C:\Users\Julia\Mon projet"),
    ("file://server/share/projet", r"\\server\share\projet"),
    ("file:///C:/Users/Julia/Projet%2520", r"C:\Users\Julia\Projet%20"),
])
def test_windows_workspace_uri_is_a_native_path(tmp_path, monkeypatch, uri, expected):
    monkeypatch.setattr(sys, "platform", "win32")
    (tmp_path / "workspace.json").write_text(json.dumps({"folder": uri}))
    assert _workspace_folder(tmp_path / "chatSessions/chat.json") == expected


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows executable locking")
def test_native_update_while_previous_runtime_is_running(tmp_path):
    destination = tmp_path / "installed runtime.exe"
    source = Path(sys.executable).with_name("pythonw.exe")
    shutil.copy2(sys.executable, destination)
    ready = tmp_path / "ready"
    env = {**os.environ, "PYTHONHOME": sys.base_prefix,
           "PATH": sys.base_prefix + os.pathsep + os.environ.get("PATH", "")}
    process = subprocess.Popen([str(destination), "-c",
        f"from pathlib import Path; import time; Path({str(ready)!r}).touch(); time.sleep(60)"],
        env=env, creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.1)
        assert ready.exists(), "Runtime probe did not start"
        assert installer._copy_runtime_file(source, destination)
        assert destination.read_bytes() == source.read_bytes()
        assert process.poll() is None, "An update must preserve existing MCP sessions"
    finally:
        process.kill()
        process.communicate(timeout=10)
    assert not installer._copy_runtime_file(source, destination)
    assert not list(tmp_path.glob("*.previous"))


@pytest.mark.skipif(sys.platform != "win32", reason="Requires Windows Task Scheduler and WScript")
def test_native_scheduled_watcher_runs_hidden_with_correct_environment(tmp_path, monkeypatch):
    home = tmp_path / "memory with spaces"
    monkeypatch.setenv("AI_MEMORY_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "Codex space"))
    task_name = f"AI Memory Test {uuid.uuid4().hex}"
    probe = tmp_path / "probe with spaces.py"
    result_path = home / "probe.json"
    probe.write_text(
        "import ctypes, json, os, time\nfrom pathlib import Path\nfrom filelock import FileLock\n"
        "home = Path(os.environ['AI_MEMORY_HOME'])\n"
        "with FileLock(str(home / 'state/watcher.lock')):\n"
        "    (home / 'state/watcher-status.json').write_text(json.dumps({'pid': os.getpid()}))\n"
        "    console = ctypes.windll.kernel32.GetConsoleWindow()\n"
        "    (home / 'probe.json').write_text(json.dumps({'pid': os.getpid(), 'console_visible': bool(ctypes.windll.user32.IsWindowVisible(console)), 'codex': os.environ['CODEX_HOME']}))\n"
        "    while not (home / 'stop').exists(): time.sleep(.1)\n", encoding="utf-8")
    monkeypatch.setattr(installer, "resolve_aimemory_command", lambda: [sys.executable, str(probe)])
    try:
        result = installer._install_windows_task(1, task_name)
        assert result.changed, result.message
        deadline = time.monotonic() + 30
        while not result_path.exists() and time.monotonic() < deadline:
            time.sleep(.2)
        assert result_path.exists(), "Scheduled watcher did not start on the Windows runner"
        data = json.loads(result_path.read_text())
        # WScript hides the console at creation; unlike CREATE_NO_WINDOW it can
        # still allocate an invisible console handle for the child process.
        assert data["console_visible"] is False
        assert data["codex"] == str(tmp_path / "Codex space")
        assert installer._windows_task_status(task_name).running
        result_path.unlink()
        assert installer._install_windows_task(1, task_name).changed
        deadline = time.monotonic() + 30
        while not result_path.exists() and time.monotonic() < deadline:
            time.sleep(.2)
        assert result_path.exists(), "Reinstall did not restart the scheduled watcher"
        restarted = json.loads(result_path.read_text())
        assert restarted["pid"] != data["pid"]
        assert restarted["console_visible"] is False
    finally:
        (home / "stop").touch()
        subprocess.run(["schtasks", "/End", "/TN", task_name], capture_output=True, timeout=20,
                       creationflags=0x08000000)
        subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True, timeout=20,
                       creationflags=0x08000000)
