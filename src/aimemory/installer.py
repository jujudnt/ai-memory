from __future__ import annotations

import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import json
import filecmp
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from aimemory.state import atomic_write
from aimemory.processes import background_creationflags
from aimemory.config import AppPaths
from filelock import FileLock, Timeout


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
        installed = _installed_cli_helper()
        return [str(installed if installed.exists() else (_bundled_cli_helper() or Path(sys.executable)))]
    executable = shutil.which("aimemory")
    if executable:
        return [executable]
    return [sys.executable, "-m", "aimemory.cli"]


def resolve_mcp_command() -> tuple[str, list[str]]:
    if getattr(sys, "frozen", False):
        installed = _installed_cli_helper()
        return str(installed if installed.exists() else (_bundled_cli_helper() or Path(sys.executable))), ["mcp-server"]
    executable = shutil.which("aimemory-mcp")
    if executable:
        return executable, []
    return sys.executable, ["-m", "aimemory.mcp.server"]


def install_mcp_config(
    server_name: str = MCP_SERVER_NAME,
    config_path: Path | None = None,
    ai_memory_home: Path | None = None,
) -> InstallResult:
    config_path = config_path or Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    block = mcp_toml_block(server_name, ai_memory_home)
    pattern = re.compile(
        rf"(?ms)^\[mcp_servers\.{re.escape(server_name)}\]\n.*?(?=^\[|\Z)"
    )
    if pattern.search(existing):
        updated = pattern.sub(lambda match: block.rstrip() + "\n\n", existing)
    else:
        prefix = existing.rstrip() + "\n\n" if existing.strip() else ""
        updated = prefix + block
    if updated == existing:
        return InstallResult(False, "MCP config already up to date.", str(config_path))
    atomic_write(config_path, updated.encode("utf-8"))
    return InstallResult(True, "MCP config installed.", str(config_path))


def install_all_mcp_configs(
    server_name: str = MCP_SERVER_NAME,
    ai_memory_home: Path | None = None,
) -> InstallResult:
    runtime_changed = _install_cli_runtime()
    results = []
    if codex_available():
        results.append(("Codex", install_mcp_config(server_name, ai_memory_home=ai_memory_home)))
    if claude_desktop_available():
        results.append(("Claude Desktop", install_claude_desktop_mcp_config(server_name, ai_memory_home=ai_memory_home)))
    if claude_code_available():
        results.append(("Claude Code (terminal et VS Code)", install_claude_desktop_mcp_config(
            server_name, claude_code_config_path(), ai_memory_home)))
    vscode_path = vscode_user_mcp_config_path()
    if vscode_path:
        results.append(("VS Code", install_vscode_mcp_config(server_name, vscode_path, ai_memory_home)))
    changed = runtime_changed or any(result.changed for _, result in results)
    clients = ", ".join(name for name, _ in results)
    message = "Aucun client compatible détecté." if not results else (
        f"MCP config installed for {clients}."
        if changed
        else f"MCP config already up to date for {clients}."
    )
    paths = "; ".join(f"{name}: {result.path}" for name, result in results if result.path)
    return InstallResult(changed, message, paths)


def install_claude_desktop_mcp_config(
    server_name: str = MCP_SERVER_NAME,
    config_path: Path | None = None,
    ai_memory_home: Path | None = None,
) -> InstallResult:
    config_path = config_path or claude_desktop_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config = _read_json_object(config_path)
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    desired = mcp_json_server(ai_memory_home)
    existing = servers.get(server_name)
    servers[server_name] = desired
    config["mcpServers"] = servers
    if existing == desired and config_path.exists():
        return InstallResult(False, "Claude Desktop MCP config already up to date.", str(config_path))
    atomic_write(config_path, (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return InstallResult(True, "Claude Desktop MCP config installed.", str(config_path))


def install_vscode_mcp_config(
    server_name: str = MCP_SERVER_NAME,
    config_path: Path | None = None,
    ai_memory_home: Path | None = None,
) -> InstallResult:
    config_path = config_path or vscode_user_mcp_config_path()
    if not config_path:
        return InstallResult(False, "VS Code not detected.", None)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config = _read_json_object(config_path)
    servers = config.get("servers")
    if not isinstance(servers, dict):
        servers = {}
    desired = {"type": "stdio", **mcp_json_server(ai_memory_home)}
    existing = servers.get(server_name)
    servers[server_name] = desired
    config["servers"] = servers
    if existing == desired and config_path.exists():
        return InstallResult(False, "VS Code MCP config already up to date.", str(config_path))
    atomic_write(config_path, (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return InstallResult(True, "VS Code MCP config installed.", str(config_path))


def claude_desktop_available() -> bool:
    path = claude_desktop_config_path()
    if path.exists() or path.parent.exists():
        return True
    if platform.system().lower() == "darwin":
        return Path("/Applications/Claude.app").exists() or (Path.home() / "Applications" / "Claude.app").exists()
    return False


def claude_desktop_config_path() -> Path:
    system = platform.system().lower()
    if system == "darwin":
        return Path("~/Library/Application Support/Claude/claude_desktop_config.json").expanduser()
    if system == "windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Claude" / "claude_desktop_config.json"
    return Path("~/.config/Claude/claude_desktop_config.json").expanduser()


def codex_available() -> bool:
    return bool(shutil.which("codex") or Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().exists()
                or Path("/Applications/Codex.app").exists())


def claude_code_config_path() -> Path:
    directory = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(directory).expanduser() / ".claude.json" if directory else Path.home() / ".claude.json"


def claude_code_available() -> bool:
    return bool(shutil.which("claude") or claude_code_config_path().exists()
                or Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser().exists()
                or any((Path.home() / ".vscode" / "extensions").glob("anthropic.claude-code-*")))


def vscode_user_mcp_config_path() -> Path | None:
    explicit = os.environ.get("AI_MEMORY_VSCODE_MCP_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    system = platform.system().lower()
    candidates: list[Path]
    if system == "darwin":
        support = Path("~/Library/Application Support").expanduser()
        candidates = [
            support / "Code" / "User" / "mcp.json",
            support / "Code - Insiders" / "User" / "mcp.json",
            support / "VSCodium" / "User" / "mcp.json",
        ]
    elif system == "windows":
        appdata = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        candidates = [
            appdata / "Code" / "User" / "mcp.json",
            appdata / "Code - Insiders" / "User" / "mcp.json",
            appdata / "VSCodium" / "User" / "mcp.json",
        ]
    else:
        config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        candidates = [
            config / "Code" / "User" / "mcp.json",
            config / "Code - Insiders" / "User" / "mcp.json",
            config / "VSCodium" / "User" / "mcp.json",
        ]
    for candidate in candidates:
        if candidate.exists() or candidate.parent.exists():
            return candidate
    if shutil.which("code"):
        return candidates[0]
    return None


def mcp_json_server(ai_memory_home: Path | None = None) -> dict:
    command, args = resolve_mcp_command()
    server: dict = {"command": command}
    if args:
        server["args"] = args
    if ai_memory_home:
        server["env"] = {"AI_MEMORY_HOME": str(ai_memory_home.expanduser())}
    return server


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


def _read_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Configuration MCP illisible (fichier conservé): {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Configuration Claude Desktop invalide: {path}")
    return data


def install_watcher_service(interval_seconds: float = 10.0, force: bool = False) -> InstallResult:
    runtime_changed = _install_cli_runtime()
    status = get_watcher_service_status()
    expected_command = [*resolve_aimemory_command(), "watch", "--interval", str(interval_seconds)]
    if not force and not runtime_changed and status.installed and status.running and (
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


def _install_windows_task(interval_seconds: float, task_name: str = "AI Memory Watcher") -> InstallResult:
    paths = AppPaths.from_env()
    paths.ensure()
    command = [*resolve_aimemory_command(), "watch", "--interval", str(interval_seconds)]
    launcher = paths.state / "watcher-launch.vbs"
    # Task Scheduler does not inherit CREATE_NO_WINDOW from schtasks.exe.
    # WScript keeps both its own window and the long-running console child hidden.
    lines = ['Set shell = CreateObject("WScript.Shell")']
    environment = {"AI_MEMORY_HOME": str(paths.home.resolve())}
    environment.update({key: os.environ[key] for key in ("CODEX_HOME", "CLAUDE_CONFIG_DIR") if key in os.environ})
    for key, value in environment.items():
        lines.append(f'shell.Environment("PROCESS")({_vbs_string(key)}) = {_vbs_string(value)}')
    lines.append(f'WScript.Quit shell.Run({_vbs_string(subprocess.list2cmdline(command))}, 0, True)')
    atomic_write(launcher, ("\r\n".join(lines) + "\r\n").encode("utf-16"))
    identity_output = subprocess.run(["whoami", "/USER", "/FO", "CSV", "/NH"], text=True,
                                     errors="replace", capture_output=True, check=True,
                                     creationflags=background_creationflags(), timeout=10).stdout
    identity_match = re.search(r"S-1-(?:\d+-)*\d+", identity_output)
    if not identity_match:
        raise RuntimeError("Impossible d'identifier l'utilisateur Windows pour le démarrage automatique.")
    identity = identity_match.group(0)
    xml_path = paths.state / "watcher-task.xml"
    atomic_write(xml_path, _windows_task_xml(launcher, identity))
    result = subprocess.run(
        ["schtasks", "/Create", "/F", "/TN", task_name, "/XML", str(xml_path)],
        text=True,
        capture_output=True,
        check=False,
        creationflags=background_creationflags(),
        timeout=30,
    )
    if result.returncode != 0:
        return InstallResult(False, result.stderr.strip() or result.stdout.strip())
    # A registered task can still be executing the previous version.
    subprocess.run(["schtasks", "/End", "/TN", task_name], check=False, capture_output=True,
                   creationflags=background_creationflags(), timeout=30)
    subprocess.run(["schtasks", "/Run", "/TN", task_name], check=True, capture_output=True,
                   creationflags=background_creationflags(), timeout=30)
    return InstallResult(True, "Watcher scheduled task installed.", task_name)


def _vbs_string(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _windows_task_xml(launcher: Path, identity: str) -> bytes:
    task = ET.Element("Task", {"version": "1.2", "xmlns": "http://schemas.microsoft.com/windows/2004/02/mit/task"})
    trigger = ET.SubElement(ET.SubElement(task, "Triggers"), "LogonTrigger")
    ET.SubElement(trigger, "Enabled").text = "true"
    ET.SubElement(trigger, "UserId").text = identity
    principal = ET.SubElement(ET.SubElement(task, "Principals"), "Principal", {"id": "Author"})
    for key, value in {"UserId": identity, "LogonType": "InteractiveToken", "RunLevel": "LeastPrivilege"}.items():
        ET.SubElement(principal, key).text = value
    settings = ET.SubElement(task, "Settings")
    for key, value in {
        "MultipleInstancesPolicy": "IgnoreNew", "DisallowStartIfOnBatteries": "false",
        "StopIfGoingOnBatteries": "false", "StartWhenAvailable": "true",
        "ExecutionTimeLimit": "PT0S",
    }.items():
        ET.SubElement(settings, key).text = value
    restart = ET.SubElement(settings, "RestartOnFailure")
    ET.SubElement(restart, "Interval").text = "PT1M"
    ET.SubElement(restart, "Count").text = "3"
    action = ET.SubElement(ET.SubElement(task, "Actions", {"Context": "Author"}), "Exec")
    ET.SubElement(action, "Command").text = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wscript.exe")
    ET.SubElement(action, "Arguments").text = subprocess.list2cmdline(["//B", "//Nologo", str(launcher)])
    return ET.tostring(task, encoding="utf-16", xml_declaration=True)


def _windows_task_status(task_name: str = "AI Memory Watcher") -> WatcherServiceStatus:
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", task_name, "/XML"],
        text=True,
        errors="replace",
        capture_output=True,
        check=False,
        creationflags=background_creationflags(),
        timeout=10,
    )
    if result.returncode != 0:
        return WatcherServiceStatus(False, False, task_name, (result.stderr or result.stdout).strip())
    running = False
    # The process lock is language-independent and also covers a watcher started
    # from the interface. Scheduler output is localized on French Windows.
    state = AppPaths.from_env().state
    if state.is_dir():
        try:
            with FileLock(str(state / "watcher.lock"), timeout=0):
                pass
        except Timeout:
            running = True
    return WatcherServiceStatus(
        installed=True,
        running=running,
        path=task_name,
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
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.extend([
            Path(bundle_root) / "ai-memory-cli",
            Path(bundle_root) / "ai-memory-cli.exe",
        ])
    # macOS .app bundle: Contents/MacOS/AI Memory -> Contents/MacOS/ai-memory-cli.
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _installed_cli_helper() -> Path:
    if platform.system().lower() == "windows":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "AI Memory" / "bin" / "ai-memory-cli.exe"
    return Path.home() / ".ai-memory" / "bin" / "ai-memory-cli"


def _install_cli_runtime() -> bool:
    if not getattr(sys, "frozen", False):
        return False
    source = _bundled_cli_helper() or Path(sys.executable)
    _validate_cli_runtime(source)
    destination = _installed_cli_helper()
    changed = _copy_runtime_file(source, destination)

    rclone_name = "rclone.exe" if os.name == "nt" else "rclone"
    rclone_candidates = [
        source.with_name(rclone_name),
        Path(sys.executable).with_name(rclone_name),
        Path(getattr(sys, "_MEIPASS", ".")) / rclone_name,
    ]
    for rclone_source in rclone_candidates:
        if rclone_source.is_file():
            changed = _copy_runtime_file(rclone_source, destination.with_name(rclone_name)) or changed
            break
    return changed


def _validate_cli_runtime(executable: Path) -> None:
    try:
        result = subprocess.run([str(executable), "--help"], capture_output=True, timeout=30,
                                creationflags=background_creationflags())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Le composant AI Memory ne démarre pas. L'installation existante est conservée.") from exc
    if result.returncode:
        raise RuntimeError("Le composant AI Memory ne démarre pas. L'installation existante est conservée.")


def _copy_runtime_file(source: Path, destination: Path) -> bool:
    # Windows retains running image files; remove only our unlocked old copies.
    for previous in destination.parent.glob(f".{destination.name}.*.previous"):
        try:
            previous.unlink()
        except OSError:
            pass
    try:
        if source.resolve() == destination.resolve():
            return False
    except OSError:
        pass
    if destination.exists() and filecmp.cmp(source, destination, shallow=False):
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    shutil.copy2(source, temporary)
    temporary.chmod(0o755)
    try:
        os.replace(temporary, destination)
    except PermissionError:
        if sys.platform != "win32" or not destination.is_file():
            temporary.unlink(missing_ok=True)
            raise
        previous = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.previous")
        try:
            os.replace(destination, previous)
            try:
                os.replace(temporary, destination)
            except OSError:
                os.replace(previous, destination)
                raise
        finally:
            temporary.unlink(missing_ok=True)
        try:
            previous.unlink()
        except OSError:
            pass
    return True
