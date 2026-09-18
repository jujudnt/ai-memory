from __future__ import annotations

import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


MCP_SERVER_NAME = "ai-memory"
WATCHER_LABEL = "io.github.jujudnt.ai-memory.watcher"


@dataclass(slots=True)
class InstallResult:
    changed: bool
    message: str
    path: str | None = None


@dataclass(slots=True)
class WatcherServiceStatus:
    installed: bool
    running: bool
    path: str | None = None
    detail: str | None = None
    command: list[str] | None = None


def resolve_aimemory_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(_bundled_cli_helper() or Path(sys.executable))]
    executable = shutil.which("aimemory")
    if executable:
        return [executable]
    return [sys.executable, "-m", "aimemory.cli"]


def resolve_mcp_command() -> tuple[str, list[str]]:
    if getattr(sys, "frozen", False):
        return str(_bundled_cli_helper() or Path(sys.executable)), ["mcp-server"]
    executable = shutil.which("aimemory-mcp")
    if executable:
        return executable, []
    return sys.executable, ["-m", "aimemory.mcp.server"]


def install_mcp_config(
    server_name: str = MCP_SERVER_NAME,
    config_path: Path | None = None,
    ai_memory_home: Path | None = None,
) -> InstallResult:
    config_path = config_path or Path("~/.codex/config.toml").expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    block = mcp_toml_block(server_name, ai_memory_home)
    pattern = re.compile(
        rf"(?ms)^\[mcp_servers\.{re.escape(server_name)}\]\n.*?(?=^\[|\Z)"
    )
    if pattern.search(existing):
        updated = pattern.sub(block.rstrip() + "\n\n", existing)
    else:
        prefix = existing.rstrip() + "\n\n" if existing.strip() else ""
        updated = prefix + block
    if updated == existing:
        return InstallResult(False, "MCP config already up to date.", str(config_path))
    config_path.write_text(updated, encoding="utf-8")
    return InstallResult(True, "MCP config installed.", str(config_path))


def mcp_toml_block(
    server_name: str = MCP_SERVER_NAME,
    ai_memory_home: Path | None = None,
) -> str:
    command, args = resolve_mcp_command()
    lines = [
        f"[mcp_servers.{server_name}]",
        f'command = "{_toml_string(command)}"',
    ]
    if args:
        lines.append("args = [" + ", ".join(f'"{_toml_string(arg)}"' for arg in args) + "]")
    if ai_memory_home:
        lines.append(f'env = {{ AI_MEMORY_HOME = "{_toml_string(str(ai_memory_home.expanduser()))}" }}')
    lines.extend(
        [
            "startup_timeout_sec = 10",
            "tool_timeout_sec = 60",
            'default_tools_approval_mode = "auto"',
            "",
        ]
    )
    return "\n".join(lines)


def install_watcher_service(interval_seconds: float = 10.0) -> InstallResult:
    status = get_watcher_service_status()
    expected_command = [*resolve_aimemory_command(), "watch", "--interval", str(interval_seconds)]
    if status.installed and status.running and (
        status.command is None or status.command == expected_command
    ):
        return InstallResult(False, "Watcher is already installed and running.", status.path)

    system = platform.system().lower()
    if system == "darwin":
        return _install_launch_agent(interval_seconds)
    if system == "windows":
        return _install_windows_task(interval_seconds)
    if system == "linux":
        return _install_systemd_user_service(interval_seconds)
    return InstallResult(False, f"Unsupported platform: {platform.system()}")


def get_watcher_service_status() -> WatcherServiceStatus:
    system = platform.system().lower()
    if system == "darwin":
        return _launch_agent_status()
    if system == "windows":
        return _windows_task_status()
    if system == "linux":
        return _systemd_user_status()
    return WatcherServiceStatus(False, False, detail=f"Unsupported platform: {platform.system()}")


def _install_launch_agent(interval_seconds: float) -> InstallResult:
    args = [*resolve_aimemory_command(), "watch", "--interval", str(interval_seconds)]
    home = Path.home()
    plist_path = home / "Library" / "LaunchAgents" / f"{WATCHER_LABEL}.plist"
    logs_dir = home / ".ai-memory" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": WATCHER_LABEL,
        "ProgramArguments": args,
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(logs_dir / "watcher.out.log"),
        "StandardErrorPath": str(logs_dir / "watcher.err.log"),
        "WorkingDirectory": str(home),
    }
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    with plist_path.open("wb") as handle:
        plistlib.dump(plist, handle)

    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist_path)], check=False)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], check=False)
    subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/{WATCHER_LABEL}"], check=False)
    return InstallResult(True, "Watcher LaunchAgent installed and started.", str(plist_path))


def _launch_agent_status() -> WatcherServiceStatus:
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{WATCHER_LABEL}.plist"
    installed = plist_path.exists()
    if not installed:
        return WatcherServiceStatus(False, False, str(plist_path))
    command: list[str] | None = None
    try:
        with plist_path.open("rb") as handle:
            plist = plistlib.load(handle)
        program_args = plist.get("ProgramArguments")
        if isinstance(program_args, list) and all(isinstance(arg, str) for arg in program_args):
            command = program_args
    except (OSError, plistlib.InvalidFileException):
        command = None
    uid = os.getuid()
    result = subprocess.run(
        ["launchctl", "print", f"gui/{uid}/{WATCHER_LABEL}"],
        text=True,
        capture_output=True,
        check=False,
    )
    return WatcherServiceStatus(
        installed=True,
        running=result.returncode == 0 and "state = running" in result.stdout,
        path=str(plist_path),
        detail=(result.stderr or result.stdout).strip()[:500] if result.returncode != 0 else None,
        command=command,
    )


def _install_windows_task(interval_seconds: float) -> InstallResult:
    command = " ".join(_quote_win(part) for part in [*resolve_aimemory_command(), "watch", "--interval", str(interval_seconds)])
    result = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/F",
            "/SC",
            "ONLOGON",
            "/TN",
            "AI Memory Watcher",
            "/TR",
            command,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return InstallResult(False, result.stderr.strip() or result.stdout.strip())
    return InstallResult(True, "Watcher scheduled task installed.", "AI Memory Watcher")


def _windows_task_status() -> WatcherServiceStatus:
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", "AI Memory Watcher", "/FO", "LIST", "/V"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return WatcherServiceStatus(False, False, "AI Memory Watcher", (result.stderr or result.stdout).strip())
    output = result.stdout
    return WatcherServiceStatus(
        installed=True,
        running="Status:" in output and "Running" in output,
        path="AI Memory Watcher",
        detail=output.strip()[:500],
    )


def _install_systemd_user_service(interval_seconds: float) -> InstallResult:
    command = " ".join(_quote_shell(part) for part in [*resolve_aimemory_command(), "watch", "--interval", str(interval_seconds)])
    service_dir = Path("~/.config/systemd/user").expanduser()
    service_path = service_dir / "ai-memory-watcher.service"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_path.write_text(
        f"""[Unit]
Description=AI Memory watcher

[Service]
ExecStart={command}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
""",
        encoding="utf-8",
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "--now", "ai-memory-watcher.service"], check=False)
    return InstallResult(True, "Watcher systemd user service installed.", str(service_path))


def _systemd_user_status() -> WatcherServiceStatus:
    service_path = Path("~/.config/systemd/user/ai-memory-watcher.service").expanduser()
    active = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", "ai-memory-watcher.service"],
        check=False,
    )
    enabled = subprocess.run(
        ["systemctl", "--user", "is-enabled", "--quiet", "ai-memory-watcher.service"],
        check=False,
    )
    installed = service_path.exists() or enabled.returncode == 0
    return WatcherServiceStatus(installed, active.returncode == 0, str(service_path))


def _toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _quote_shell(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _quote_win(value: str) -> str:
    escaped = value.replace('"', r"\"")
    return f'"{escaped}"' if " " in escaped else escaped


def _bundled_cli_helper() -> Path | None:
    executable = Path(sys.executable)
    candidates = [
        executable.with_name("ai-memory-cli"),
        executable.with_name("ai-memory-cli.exe"),
    ]
    # macOS .app bundle: Contents/MacOS/AI Memory -> Contents/MacOS/ai-memory-cli.
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None
