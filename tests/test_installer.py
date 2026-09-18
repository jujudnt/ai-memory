from pathlib import Path
from unittest.mock import patch

from aimemory.installer import install_mcp_config, mcp_toml_block, resolve_mcp_command


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
