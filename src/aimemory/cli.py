from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from aimemory.cloud import LocalFolderProvider
from aimemory.service import MemoryService
from aimemory.sync import SyncService
from aimemory.watcher import WatcherService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aimemory")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Show local AI Memory status.")

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

    watch_parser = sub.add_parser("watch", help="Continuously collect Codex sessions.")
    watch_parser.add_argument("--codex-home", type=Path, default=None)
    watch_parser.add_argument("--interval", type=float, default=10.0)
    watch_parser.add_argument("--once", action="store_true")

    sub.add_parser("watch-status", help="Show the latest watcher status.")

    mcp_parser = sub.add_parser("mcp-config", help="Print Codex MCP setup snippets.")
    mcp_parser.add_argument("--server-name", default="ai-memory")
    mcp_parser.add_argument("--ai-memory-home", type=Path, default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = MemoryService()

    if args.command == "doctor":
        print(json.dumps(service.status(), indent=2, sort_keys=True))
        return 0

    if args.command == "import-codex":
        result = service.import_codex(codex_home=args.codex_home, force=args.force)
        print(json.dumps(result.__dict__, indent=2, sort_keys=True))
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

    raise SystemExit(f"Unknown command: {args.command}")


def _mcp_config(server_name: str, home: Path | None) -> str:
    executable = shutil.which("aimemory-mcp")
    if executable:
        command = executable
        args: list[str] = []
    else:
        command = sys.executable
        args = ["-m", "aimemory.mcp.server"]

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

    cli_command = f"codex mcp add {server_name}{cli_env} -- {command}"
    if args:
        cli_command += " " + " ".join(args)

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
