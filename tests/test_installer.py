import json
import os
import tomllib
from pathlib import Path
from unittest.mock import patch

from aimemory.installer import (
    InstallResult,
    WatcherServiceStatus,
    install_claude_desktop_mcp_config,
    install_all_mcp_configs,
    install_vscode_mcp_config,
    install_mcp_config,
    install_watcher_service,
    mcp_toml_block,
    mcp_json_server,
    resolve_aimemory_command,
    resolve_mcp_command,
)
from aimemory.service import MemoryService


def test_mcp_toml_block_contains_stdio_command():
    block = mcp_toml_block("ai-memory")

    assert "[mcp_servers.ai-memory]" in block
    assert "command =" in block
    assert "startup_timeout_sec = 10" in block


def test_install_mcp_config_replaces_existing_block(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text(
        """
[mcp_servers.ai-memory]
command = "old"

[other]
value = true
""".lstrip(),
        encoding="utf-8",
    )

    result = install_mcp_config(config_path=config)
    text = config.read_text(encoding="utf-8")

    assert result.changed is True
    assert text.count("[mcp_servers.ai-memory]") == 1
    assert 'command = "old"' not in text
    assert "[other]" in text


def test_install_claude_desktop_config_preserves_other_servers(tmp_path: Path):
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(
        '{"mcpServers": {"other": {"command": "other"}}}\n',
        encoding="utf-8",
    )

    result = install_claude_desktop_mcp_config(config_path=config)
    data = json.loads(config.read_text(encoding="utf-8"))

    assert result.changed is True
    assert data["mcpServers"]["other"]["command"] == "other"
    assert data["mcpServers"]["ai-memory"] == mcp_json_server()


def test_install_vscode_mcp_config_writes_user_mcp_json(tmp_path: Path):
    config = tmp_path / "mcp.json"
    config.write_text('{"servers": {"playwright": {"command": "npx"}}}\n', encoding="utf-8")

    result = install_vscode_mcp_config(config_path=config)
    data = json.loads(config.read_text(encoding="utf-8"))

    assert result.changed is True
    assert data["servers"]["playwright"]["command"] == "npx"
    assert data["servers"]["ai-memory"]["type"] == "stdio"
    assert data["servers"]["ai-memory"]["command"] == mcp_json_server()["command"]


def test_frozen_mcp_command_reuses_app_executable():
    with (
        patch("sys.frozen", True, create=True),
        patch("sys.executable", "/tmp/AI Memory"),
        patch("aimemory.installer._installed_cli_helper", return_value=Path("/missing/ai-memory-cli")),
    ):
        command, args = resolve_mcp_command()

    assert Path(command) == Path("/tmp/AI Memory")
    assert args == ["mcp-server"]


def test_frozen_mcp_command_prefers_bundled_helper(tmp_path: Path):
    app_binary = tmp_path / "AI Memory.app" / "Contents" / "MacOS" / "AI Memory"
    helper = app_binary.with_name("ai-memory-cli")
    helper.parent.mkdir(parents=True)
    app_binary.write_text("", encoding="utf-8")
    helper.write_text("", encoding="utf-8")

    installed = tmp_path / "missing" / "ai-memory-cli"
    with (
        patch("sys.frozen", True, create=True),
        patch("sys.executable", str(app_binary)),
        patch("aimemory.installer._installed_cli_helper", return_value=installed),
    ):
        command, args = resolve_mcp_command()

    assert command == str(helper)
    assert args == ["mcp-server"]


def test_frozen_commands_prefer_installed_runtime_after_app_moves(tmp_path: Path):
    installed = tmp_path / ".ai-memory" / "bin" / "ai-memory-cli"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"standalone")

    with (
        patch("sys.frozen", True, create=True),
        patch("sys.executable", "/Volumes/Anywhere/AI Memory.app/Contents/MacOS/AI Memory"),
        patch("aimemory.installer._installed_cli_helper", return_value=installed),
    ):
        watcher_command = resolve_aimemory_command()
        mcp_command, mcp_args = resolve_mcp_command()

    assert watcher_command == [str(installed)]
    assert mcp_command == str(installed)
    assert mcp_args == ["mcp-server"]


def test_install_all_mcp_configs_copies_frozen_runtime_to_stable_path(tmp_path: Path, monkeypatch):
    app_binary = tmp_path / "Downloads" / "AI Memory.app" / "Contents" / "MacOS" / "AI Memory"
    helper = app_binary.with_name("ai-memory-cli")
    installed = tmp_path / ".ai-memory" / "bin" / "ai-memory-cli"
    helper.parent.mkdir(parents=True)
    app_binary.write_bytes(b"desktop")
    helper.write_bytes(b"standalone-mcp")
    codex_config = tmp_path / ".codex" / "config.toml"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    with (
        patch("sys.frozen", True, create=True),
        patch("sys.executable", str(app_binary)),
        patch("aimemory.installer._installed_cli_helper", return_value=installed),
        patch("aimemory.installer.claude_desktop_available", return_value=False),
        patch("aimemory.installer.vscode_user_mcp_config_path", return_value=None),
    ):
        result = install_all_mcp_configs()

    assert result.changed is True
    assert installed.read_bytes() == b"standalone-mcp"
    if os.name != "nt":
        assert installed.stat().st_mode & 0o111
    config = tomllib.loads(codex_config.read_text(encoding="utf-8"))
    assert config["mcp_servers"]["ai-memory"]["command"] == str(installed)


def test_install_watcher_short_circuits_when_already_running():
    with patch(
        "aimemory.installer.get_watcher_service_status",
        return_value=WatcherServiceStatus(True, True, "/tmp/watcher", command=None),
    ):
        result = install_watcher_service()

    assert result.changed is False
    assert result.message == "Watcher is already installed and running."
    assert result.path == "/tmp/watcher"


def test_install_watcher_reinstalls_when_launch_agent_command_changed():
    with (
        patch(
            "aimemory.installer.get_watcher_service_status",
            return_value=WatcherServiceStatus(
                True,
                True,
                "/tmp/watcher.plist",
                command=["/old/AI Memory", "watch", "--interval", "10.0"],
            ),
        ),
        patch("aimemory.installer.resolve_aimemory_command", return_value=["/new/ai-memory-cli"]),
        patch("platform.system", return_value="Darwin"),
        patch(
            "aimemory.installer._install_launch_agent",
            return_value=InstallResult(True, "Watcher LaunchAgent installed and started.", "/tmp/watcher.plist"),
        ) as install_launch_agent,
    ):
        result = install_watcher_service()

    assert result.changed is True
    install_launch_agent.assert_called_once_with(10.0)


def test_status_exposes_local_storage(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AI_MEMORY_HOME", str(tmp_path / "memory"))
    status = MemoryService().status()

    assert status["storage"]["provider"] == "local-folder"
    assert status["storage"]["cloud_sync"] == "not-configured"


def test_reinstall_mcp_preserves_windows_path_escaping(tmp_path):
    import tomllib
    config = tmp_path / "config.toml"
    command = r"C:\Users\Julia\AI Memory.exe"
    with patch("aimemory.installer.resolve_mcp_command", return_value=(command, ["mcp-server"])):
        install_mcp_config(config_path=config)
        install_mcp_config(config_path=config)
    assert tomllib.loads(config.read_text())["mcp_servers"]["ai-memory"]["command"] == command
