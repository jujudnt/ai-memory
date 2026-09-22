"""Windows simulations on every platform, plus a native console check on Windows."""
import ast
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aimemory import installer
from aimemory.processes import background_creationflags


@pytest.mark.parametrize("platform,expected", [("win32", 0x08000000), ("darwin", 0), ("linux", 0)])
def test_background_flags(platform, expected, monkeypatch):
    monkeypatch.setattr(sys, "platform", platform)
    assert background_creationflags() == expected


def test_windows_scheduler_commands_are_hidden(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="Status: Running", stderr=""))
    monkeypatch.setattr(installer.subprocess, "run", run)
    monkeypatch.setattr(installer, "resolve_aimemory_command", lambda: [r"C:\AI Memory\ai-memory-cli.exe"])
    assert installer._install_windows_task(10).changed
    for _ in range(5):
        assert installer._windows_task_status().installed
    installer._validate_cli_runtime(Path("fake.exe"))
    assert len(run.call_args_list) == 8
    for call in run.call_args_list:
        assert call.kwargs["creationflags"] == 0x08000000
        assert call.kwargs["timeout"] > 0
        assert call.kwargs["capture_output"] is True


def test_windows_missing_task_is_hidden_too(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    run = Mock(return_value=SimpleNamespace(returncode=1, stdout="", stderr="not found"))
    monkeypatch.setattr(installer.subprocess, "run", run)
    assert not installer._windows_task_status().installed
    assert run.call_args.kwargs["creationflags"] == 0x08000000


def test_all_cloud_subprocesses_explicitly_hide_the_console():
    # Guard every cloud subprocess, including OAuth, transfer and retry paths.
    for file in ("cloud/google_drive.py", "cloud/rclone_provider.py", "sync/cloud_sync.py"):
        tree = ast.parse((Path("src/aimemory") / file).read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                 and n.func.value.id == "subprocess" and n.func.attr in {"run", "Popen"}]
        assert calls
        for call in calls:
            assert "creationflags" in {keyword.arg for keyword in call.keywords}, (file, call.lineno)


@pytest.mark.skipif(sys.platform != "win32", reason="Requires the Windows process API")
def test_native_child_has_no_console():
    result = subprocess.run(
        [sys.executable, "-c", "import ctypes; print(int(ctypes.windll.kernel32.GetConsoleWindow() or 0))"],
        creationflags=background_creationflags(), capture_output=True, text=True, check=True, timeout=15,
    )
    assert result.stdout.strip() == "0"


@pytest.mark.skipif(sys.platform != "win32", reason="Requires pythonw and the Windows process API")
def test_windowless_parent_hides_console_children(tmp_path):
    import json
    probe = tmp_path / "probe.py"
    result_file = tmp_path / "result.json"
    probe.write_text(
        "import ctypes, json, subprocess\n"
        "from pathlib import Path\n"
        "from aimemory.processes import background_creationflags\n"
        f"command = {[sys.executable, '-c', 'import ctypes; print(int(ctypes.windll.kernel32.GetConsoleWindow() or 0))']!r}\n"
        "def child(flags):\n"
        "    return subprocess.run(command, creationflags=flags, capture_output=True, text=True, check=True, timeout=15).stdout.strip()\n"
        "result = {'parent': int(ctypes.windll.kernel32.GetConsoleWindow() or 0),\n"
        "          'hidden': child(background_creationflags()), 'control': child(subprocess.CREATE_NEW_CONSOLE)}\n"
        f"Path({str(result_file)!r}).write_text(json.dumps(result))\n",
        encoding="utf-8",
    )
    subprocess.run([str(Path(sys.executable).with_name("pythonw.exe")), str(probe)],
                   check=True, timeout=45)
    data = json.loads(result_file.read_text())
    assert data["parent"] == 0
    assert data["hidden"] == "0"
    assert data["control"] != "0", "Positive control must actually allocate a console"
