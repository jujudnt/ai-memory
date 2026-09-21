"""Reproduce workflow defects using disposable data; never contact a real cloud.

Run from the repository: .venv/bin/python scripts/audit_workflow.py
The output describes observed behavior, not a passing regression-test suite.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_cloud_integrity import memory, session

from aimemory.adapters.claude import ClaudeAdapter
from aimemory.cloud.rclone_provider import _friendly_rclone_error
from aimemory.desktop import _pull_current_destination
from aimemory.mcp.tools import MemoryToolHandlers
from aimemory.state import read_json, write_json
from aimemory.sync.cloud_sync import LocalArchiveRemote


def connect(service, root: Path, identity: str) -> None:
    write_json(service.paths.state / "cloud.json", {
        "provider": "local-folder", "root": str(root), "connection_id": identity,
    })


def objects(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def migration(root: Path) -> dict:
    service = memory(root / "memory")
    source = root / "source"
    session(source, text="older")
    service.import_codex(source)
    connect(service, root / "old", "old")
    service.sync_now()
    session(source, text="newer", timestamp="2026-09-19T10:00:00Z")
    service.import_codex(source)
    service.sync_now()
    _pull_current_destination(service)
    before = objects(root / "old")
    connect(service, root / "new", "new")
    service.sync_now()
    after = objects(root / "new")
    return {
        "old_objects": len(before), "new_objects": len(after),
        "raw_missing_from_new": sum(p.startswith("raw/") for p in before - after),
        "snapshots_missing_from_new": sum(p.startswith("snapshots/") for p in before - after),
    }


def checkpoint(root: Path) -> dict:
    first = memory(root / "a")
    source = root / "source"
    session(source)
    first.import_codex(source)
    connect(first, root / "remote", "remote")
    first.sync_now()
    second = memory(root / "b")
    connect(second, root / "remote", "remote")
    remote = LocalArchiveRemote(root / "remote")
    calls = []

    class InterruptedRemote:
        def list(self, progress):
            return remote.list(progress)

        def download(self, relative, path):
            calls.append(relative)
            if len(calls) == 2:
                raise RuntimeError("Simulated network outage")
            remote.download(relative, path)

    with patch("aimemory.sync.cloud_sync._remote_archive", return_value=InterruptedRemote()):
        try:
            second.sync_now()
        except RuntimeError:
            pass
    return {
        "completed_downloads_before_failure": len(calls) - 1,
        "persisted_confirmations": len(read_json(second.paths.state / "synced-objects.json")),
        "incoming_preserved": (second.paths.cache / "incoming").exists(),
    }


def raw_growth(root: Path) -> dict:
    service = memory(root / "memory")
    source = root / "source"
    path = session(source)
    service.import_codex(source)
    connect(service, root / "remote", "remote")
    service.sync_now()
    with path.open("a") as stream:
        stream.write(json.dumps({
            "type": "response_item", "timestamp": "2026-09-19T12:00:00Z",
            "payload": {"type": "message", "role": "assistant", "content": "One extra answer"},
        }) + "\n")
    service.import_codex(source)
    entry = read_json(service.paths.state / "source-manifest.json")[str(path)]
    return {"incremental_after_cleanup": entry["raw_path"].endswith(".delta.json.gz")}


def claude_subagents(root: Path) -> dict:
    adapter = ClaudeAdapter(claude_home=root / "claude")

    def record(text, timestamp):
        return (json.dumps({
            "sessionId": "shared-parent", "timestamp": timestamp, "type": "assistant",
            "message": {"role": "assistant", "content": text},
        }) + "\n").encode()

    main = adapter.parse_session(
        root / "claude/projects/folder/shared-parent.jsonl",
        record("main work", "2026-09-19T10:00:00Z"),
    )
    child = adapter.parse_session(
        root / "claude/projects/folder/shared-parent/subagents/agent-x.jsonl",
        record("subagent work", "2026-09-19T11:00:00Z"),
    )
    service = memory(root / "memory")
    service.accept_conversation(main)
    service.accept_conversation(child)
    return {
        "identity_collision": main.id == child.id,
        "indexed_conversations": len(service.list_conversations()),
        "subagent_replaced_current_main": service.get_conversation(main.id).messages[0].content == "subagent work",
    }


def transient_icloud_failure(root: Path) -> dict:
    service = memory(root / "memory")
    write_json(service.paths.state / "cloud.json", {"provider": "icloud-online"})

    class OfflineRemote:
        def list(self, progress):
            raise RuntimeError(_friendly_rclone_error("icloud-online", "dial tcp: no such host", 1))

    with patch("aimemory.sync.cloud_sync._remote_archive", return_value=OfflineRemote()):
        try:
            service.sync_now()
        except RuntimeError:
            pass
    state = read_json(service.paths.state / "sync-status.json")
    return {"automatic_retry_disabled": state.get("requires_action"), "message": state.get("error")}


def mcp_dates(root: Path) -> dict:
    service = memory(root / "memory")
    session(root / "source", text="unique audit phrase")
    service.import_codex(root / "source")
    results = MemoryToolHandlers(service).search_conversations(
        "unique audit phrase", date_from="2099-01-01", date_to="2099-01-02",
    )
    return {"results_outside_requested_dates": len(results)}


def main() -> None:
    probes = (migration, checkpoint, raw_growth, claude_subagents, transient_icloud_failure, mcp_dates)
    with TemporaryDirectory(prefix="ai-memory-audit-") as directory:
        root = Path(directory)
        for probe in probes:
            print(json.dumps({"probe": probe.__name__, **probe(root / probe.__name__)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
