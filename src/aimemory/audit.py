from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

from aimemory.adapters.codex import CodexAdapter
from aimemory.archive.raw_backup import read_raw
from aimemory.config import default_codex_home
from aimemory.state import now, read_json, write_json


def audit_codex(service, codex_home: Path | None = None) -> dict:
    adapter = CodexAdapter(codex_home)
    manifest = read_json(service.paths.state / "source-manifest.json")
    result = {"checked_at": now(), "files": 0, "verified_files": 0, "source_bytes": 0,
              "unique_sessions": 0, "issues": [], "changing_files": [], "missing_thread_ids": []}
    ids = set()
    for session in adapter.scan_sessions():
        result["files"] += 1
        entry = manifest.get(str(session.path), {})
        try:
            raw = session.path.read_bytes()
            result["source_bytes"] += len(raw)
            parsed = adapter.parse_session(session.path, raw)
            ids.add(parsed.source_session_id)
            digest = hashlib.sha256(raw).hexdigest()
            restored = read_raw(service.paths.archive, entry["raw_path"])
            if hashlib.sha256(restored).hexdigest() != entry.get("sha256"):
                raise ValueError("Raw backup checksum differs from the imported snapshot")
            if entry.get("sha256") != digest:
                result["changing_files"].append(str(session.path))
                # Validate the captured snapshot even when a live source has advanced.
                parsed = adapter.parse_session(session.path, restored)
                digest = entry["sha256"]
            elif restored != raw:
                raise ValueError("Raw backup differs from source")
            if not service.db.get_conversation_row(parsed.id):
                raise ValueError("Conversation missing from index")
            # Find the exact normalized revision, including duplicate source files.
            matched = False
            for path in (service.paths.archive / "snapshots" / parsed.id).glob("*.json.*"):
                archived = service.archive.read(path)
                if archived.metadata.get("source_sha256") != digest:
                    continue
                if archived.messages != parsed.messages or archived.tool_calls != parsed.tool_calls:
                    raise ValueError("Normalized messages/tools differ from source")
                matched = True
                break
            if not matched:
                raise ValueError("Normalized revision missing")
            result["verified_files"] += 1
        except Exception as exc:
            result["issues"].append({"path": str(session.path), "error": str(exc)})
    result["unique_sessions"] = len(ids)
    # Read Codex's index as a cross-check only, never modify it.
    root = codex_home or default_codex_home()
    thread_ids = set()
    for path in root.glob("state_*.sqlite"):
        try:
            with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as conn:
                thread_ids.update(row[0] for row in conn.execute("SELECT id FROM threads"))
        except sqlite3.Error as exc:
            result["issues"].append({"path": str(path), "error": str(exc)})
    result["indexed_codex_threads"] = len(thread_ids)
    result["missing_thread_ids"] = sorted(thread_ids - ids)
    result["ok"] = not result["issues"] and not result["missing_thread_ids"] and not result["changing_files"]
    write_json(service.paths.state / "audit.json", result)
    return result
