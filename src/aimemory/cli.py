from __future__ import annotations

import argparse
import json
import shlex
from dataclasses import asdict
from pathlib import Path

from aimemory.cloud import LocalFolderProvider
from aimemory.installer import install_mcp_config, install_watcher_service, resolve_mcp_command
from aimemory.service import MemoryService
from aimemory.sync import SyncService
from aimemory.watcher import WatcherService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aimemory")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Show local AI Memory status.")
    sub.add_parser("audit", help="Verify raw backups and normalized conversations against Codex.")
    sub.add_parser("sync", help="Synchronize the configured cloud provider.")

    sub.add_parser("import-all", help="Import Codex, Claude, and VS Code conversations.")

    import_parser = sub.add_parser("import-codex", help="Import Codex JSONL sessions.")
    import_parser.add_argument("--codex-home", type=Path, default=None)
    import_parser.add_argument("--force", action="store_true")

    search_parser = sub.add_parser("search", help="Search imported conversations.")
    search_parser.add_argument("query")
    search_parser.add_argument("--source", default=None)
    search_parser.add_argument("--project-id", default=None)
    search_parser.add_argument("--limit", type=int, default=10)

    get_parser = sub.add_parser("get", help="Print a normalized conversation as JSON.")
    get_parser.add_argument("conversation_id")

    rebuild_parser = sub.add_parser("rebuild", help="Rebuild SQLite indexes from archives.")
    rebuild_parser.set_defaults(command="rebuild")

    sync_parser = sub.add_parser("sync-local", help="Synchronize archives with a local folder.")
    sync_parser.add_argument("--folder", type=Path, required=True)
    sync_parser.add_argument(
        "--direction",
        choices=["upload", "download", "both"],
        default="both",
        help="Sync direction. Downloads are followed by a local index rebuild.",
    )

    watch_parser = sub.add_parser("watch", help="Continuously collect supported local AI conversations.")
    watch_parser.add_argument("--codex-home", type=Path, default=None)
    watch_parser.add_argument("--interval", type=float, default=10.0)
    watch_parser.add_argument("--once", action="store_true")

    sub.add_parser("watch-status", help="Show the latest watcher status.")

    mcp_parser = sub.add_parser("mcp-config", help="Print Codex MCP setup snippets.")
    mcp_parser.add_argument("--server-name", default="ai-memory")
    mcp_parser.add_argument("--ai-memory-home", type=Path, default=None)

    install_mcp_parser = sub.add_parser("install-mcp", help="Write AI Memory into Codex MCP config.")
    install_mcp_parser.add_argument("--server-name", default="ai-memory")
    install_mcp_parser.add_argument("--ai-memory-home", type=Path, default=None)

    install_watcher_parser = sub.add_parser("install-watcher", help="Install watcher at user login.")
    install_watcher_parser.add_argument("--interval", type=float, default=10.0)

    sub.add_parser("desktop", help="Open the AI Memory desktop app.")
    sub.add_parser("mcp-server", help="Run the AI Memory MCP server over stdio.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = MemoryService()

    if args.command == "audit":
        print(json.dumps(service.audit_codex(), indent=2))
        return 0
    if args.command == "sync":
        print(json.dumps(service.sync_now(), indent=2))
        return 0

    if args.command == "doctor":
        print(json.dumps(service.status(), indent=2, sort_keys=True))
        return 0

    if args.command == "import-codex":
        result = service.import_codex(codex_home=args.codex_home, force=args.force)
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
        return 0

    if args.command == "import-all":
        result = service.import_all()
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
        return 0

    if args.command == "search":
        rows = service.search(args.query, source=args.source, project_id=args.project_id, limit=args.limit)
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0

    if args.command == "get":
        conversation = service.get_conversation(args.conversation_id)
        if conversation is None:
            raise SystemExit(f"Conversation not found: {args.conversation_id}")
        print(json.dumps(conversation.to_dict(), indent=2, ensure_ascii=False, sort_keys=True))
        return 0

    if args.command == "rebuild":
        print(json.dumps({"indexed": service.rebuild_from_archives()}, indent=2, sort_keys=True))
        return 0

    if args.command == "sync-local":
        sync = SyncService(service.archive, LocalFolderProvider(args.folder))
        result = {}
        if args.direction in {"upload", "both"}:
            result.update(sync.upload_archives())
        if args.direction in {"download", "both"}:
            result.update(sync.download_archives())
            result["indexed"] = service.rebuild_from_archives()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "watch":
        WatcherService(service).run_polling(
            codex_home=args.codex_home,
            interval_seconds=args.interval,
            once=args.once,
        )
        return 0

    if args.command == "watch-status":
        print(json.dumps(service.watcher_status(), indent=2, sort_keys=True))
        return 0

    if args.command == "mcp-config":
        print(_mcp_config(args.server_name, args.ai_memory_home))
        return 0

    if args.command == "install-mcp":
        result = install_mcp_config(args.server_name, ai_memory_home=args.ai_memory_home)
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
        return 0

    if args.command == "install-watcher":
        result = install_watcher_service(interval_seconds=args.interval)
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
        return 0

    if args.command == "desktop":
        from aimemory.desktop import main as desktop_main

        return desktop_main()

    if args.command == "mcp-server":
        from aimemory.mcp.server import main as mcp_main

        mcp_main()
        return 0

    raise SystemExit(f"Unknown command: {args.command}")


def _mcp_config(server_name: str, home: Path | None) -> str:
    command, args = resolve_mcp_command()

    env_line = ""
    cli_env = ""
    if home:
        resolved_home = str(home.expanduser())
        env_line = f'\nenv = {{ AI_MEMORY_HOME = "{resolved_home}" }}'
        cli_env = f" --env AI_MEMORY_HOME={resolved_home}"

    args_line = ""
    if args:
        quoted_args = ", ".join(f'"{arg}"' for arg in args)
        args_line = f"\nargs = [{quoted_args}]"

    cli_command = f"codex mcp add {shlex.quote(server_name)}{cli_env} -- {shlex.quote(command)}"
    if args:
        cli_command += " " + " ".join(shlex.quote(arg) for arg in args)

    return f"""# CLI
{cli_command}

# ~/.codex/config.toml
[mcp_servers.{server_name}]
command = "{command}"{args_line}{env_line}
startup_timeout_sec = 10
tool_timeout_sec = 60
default_tools_approval_mode = "auto"
"""


if __name__ == "__main__":
    raise SystemExit(main())
