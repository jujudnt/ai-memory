from pathlib import Path
from unittest.mock import patch

from aimemory.installer import (
    InstallResult,
    WatcherServiceStatus,
    install_mcp_config,
    install_watcher_service,
    mcp_toml_block,
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


def test_frozen_mcp_command_reuses_app_executable():
    with patch("sys.frozen", True, create=True), patch("sys.executable", "/tmp/AI Memory"):
        command, args = resolve_mcp_command()

    assert Path(command) == Path("/tmp/AI Memory")
    assert args == ["mcp-server"]


def test_frozen_mcp_command_prefers_bundled_helper(tmp_path: Path):
    app_binary = tmp_path / "AI Memory.app" / "Contents" / "MacOS" / "AI Memory"
    helper = app_binary.with_name("ai-memory-cli")
    helper.parent.mkdir(parents=True)
    app_binary.write_text("", encoding="utf-8")
    helper.write_text("", encoding="utf-8")

    with patch("sys.frozen", True, create=True), patch("sys.executable", str(app_binary)):
        command, args = resolve_mcp_command()

    assert command == str(helper)
    assert args == ["mcp-server"]


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
