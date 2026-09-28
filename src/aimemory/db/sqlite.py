from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from aimemory.models import NormalizedConversation, ProjectIdentity
from aimemory.db.codec import pack_text, unpack_text
from aimemory.adapters.common import title_from_messages
from aimemory.sources import CODEX_SOURCES

NAME_SQL = "(SELECT n.name FROM conversation_names n WHERE n.conversation_id = c.id) AS name"


class MemoryDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False
        self._schema_lock = threading.Lock()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            conn.create_function("unpack_text", 1, unpack_text, deterministic=True)
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._schema_lock:
            if self._initialized:
                return
            self._initialize_schema()
            self._initialized = True

    def _initialize_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    cwd TEXT,
                    git_remote_normalized TEXT,
                    default_branch TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_session_id TEXT NOT NULL,
                    project_id TEXT,
                    device_id TEXT NOT NULL,
                    title TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    model TEXT,
                    archive_path TEXT NOT NULL,
                    UNIQUE(source, source_session_id),
                    FOREIGN KEY(project_id) REFERENCES projects(id),
                    FOREIGN KEY(device_id) REFERENCES devices(id)
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT,
                    ordinal INTEGER NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS compact_index_versions (
                    conversation_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_conversations_project
                ON conversations(project_id);

                CREATE INDEX IF NOT EXISTS idx_messages_conversation_role_ordinal
                ON messages(conversation_id, role, ordinal DESC);

                CREATE INDEX IF NOT EXISTS idx_messages_conversation_ordinal
                ON messages(conversation_id, ordinal);

                CREATE INDEX IF NOT EXISTS idx_conversations_device
                ON conversations(device_id);

                CREATE TABLE IF NOT EXISTS tool_calls (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    arguments TEXT,
                    output TEXT,
                    timestamp TEXT,
                    ordinal INTEGER NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS imports (
                    source TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_session_id TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    mtime REAL NOT NULL,
                    last_offset INTEGER NOT NULL,
                    last_processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(source, source_path)
                );

                CREATE INDEX IF NOT EXISTS idx_tool_calls_conversation_ordinal
                ON tool_calls(conversation_id, ordinal);

                CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(
                    conversation_id UNINDEXED,
                    source UNINDEXED,
                    project_id UNINDEXED,
                    title,
                    content
                );

                CREATE TABLE IF NOT EXISTS fts_conversation_rows (
                    fts_rowid INTEGER PRIMARY KEY,
                    conversation_id TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_fts_conversation_rows
                ON fts_conversation_rows(conversation_id);
                CREATE TABLE IF NOT EXISTS index_migrations (name TEXT PRIMARY KEY);

                -- Names the user gave a conversation, apart from its first message.
                -- origin "title": explicit title found at indexing (NULL name = none).
                -- origin "codex": live Codex thread name, refreshed on each import.
                CREATE TABLE IF NOT EXISTS conversation_names (
                    conversation_id TEXT PRIMARY KEY,
                    name TEXT,
                    origin TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_metadata (
                    conversation_id TEXT NOT NULL, field TEXT NOT NULL, value TEXT NOT NULL,
                    updated_at TEXT NOT NULL, digest TEXT NOT NULL,
                    PRIMARY KEY (conversation_id, field)
                );
                CREATE TABLE IF NOT EXISTS local_conversations (conversation_id TEXT PRIMARY KEY);
                """
            )
            # FTS UNINDEXED identifiers still require a full scan (including old
            # multi-GB content). Scan once on upgrade, then address rows directly.
            if not conn.execute("SELECT 1 FROM index_migrations WHERE name='fts-rowids-v1'").fetchone():
                conn.execute("DELETE FROM fts_conversation_rows")
                conn.execute("INSERT INTO fts_conversation_rows SELECT rowid, conversation_id FROM conversations_fts")
                conn.execute("INSERT OR IGNORE INTO index_migrations VALUES ('fts-rowids-v1')")

    def upsert_conversation(
        self,
        conversation: NormalizedConversation,
        archive_path: Path,
        source_path: Path | None = None,
        file_hash: str | None = None,
        size: int | None = None,
        mtime: float | None = None,
    ) -> None:
        self.initialize()
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO devices(id, name) VALUES (?, ?)",
                (conversation.device.id, conversation.device.name),
            )
            project_id = None
            project = conversation.project
            if project:
                latest = conn.execute("SELECT value FROM conversation_metadata WHERE field='project' "
                                      "AND json_extract(value, '$.id')=? ORDER BY updated_at DESC, digest DESC LIMIT 1", (project.id,)).fetchone()
                if latest:
                    project = ProjectIdentity(**json.loads(latest[0]))
                project_id = project.id
                conn.execute(
                    """
                    INSERT OR REPLACE INTO projects(
                        id, name, cwd, git_remote_normalized, default_branch, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM projects WHERE id = ?), CURRENT_TIMESTAMP), CURRENT_TIMESTAMP)
                    """,
                    (
                        project.id,
                        project.name,
                        project.cwd,
                        project.git_remote,
                        project.git_branch,
                        project.id,
                    ),
                )

            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation.id,))
            conn.execute("DELETE FROM tool_calls WHERE conversation_id = ?", (conversation.id,))
            conn.execute("DELETE FROM conversations_fts WHERE rowid IN (SELECT fts_rowid FROM fts_conversation_rows WHERE conversation_id=?)", (conversation.id,))
            conn.execute("DELETE FROM fts_conversation_rows WHERE conversation_id=?", (conversation.id,))
            conn.execute(
                """
                INSERT OR REPLACE INTO conversations(
                    id, source, source_session_id, project_id, device_id, title,
                    created_at, updated_at, model, archive_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation.id,
                    conversation.source,
                    conversation.source_session_id,
                    project_id,
                    conversation.device.id,
                    conversation.title,
                    conversation.created_at,
                    conversation.updated_at,
                    conversation.model,
                    str(archive_path),
                ),
            )
            for message in conversation.messages:
                message_id = _scoped_id(conversation.id, f"{message.ordinal}:{message.id}")
                conn.execute(
                    """
                    INSERT INTO messages(id, conversation_id, role, content, timestamp, ordinal)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        conversation.id,
                        message.role,
                        message.content,
                        message.timestamp,
                        message.ordinal,
                    ),
            )
            for call in conversation.tool_calls:
                call_id = _scoped_id(conversation.id, f"{call.ordinal}:{call.id}")
                conn.execute(
                    """
                    INSERT INTO tool_calls(id, conversation_id, name, arguments, output, timestamp, ordinal)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        call_id,
                        conversation.id,
                        call.name,
                        pack_text(call.arguments),
                        pack_text(call.output),
                        call.timestamp,
                        call.ordinal,
                    ),
                )
            fts_cursor = conn.execute(
                """
                INSERT INTO conversations_fts(conversation_id, source, project_id, title, content)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    conversation.id,
                    conversation.source,
                    project_id,
                    "\n".join(value for value in (conversation.title, project.name if project else None, project.cwd if project else None) if value),
                    conversation.searchable_text(),
                ),
            )
            conn.execute("INSERT INTO fts_conversation_rows VALUES (?, ?)", (fts_cursor.lastrowid, conversation.id))

            conn.execute("INSERT OR REPLACE INTO compact_index_versions VALUES (?, 1)", (conversation.id,))
            if conversation.source not in CODEX_SOURCES:
                conn.execute(
                    "INSERT INTO conversation_names VALUES (?, ?, 'title') ON CONFLICT(conversation_id) "
                    "DO UPDATE SET name = excluded.name WHERE origin = 'title'",
                    (conversation.id, explicit_title(conversation)),
                )
            self._apply_catalog_to_index(conn, conversation.id)

            if source_path and file_hash is not None and size is not None and mtime is not None:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO imports(
                        source, source_path, source_session_id, file_hash, size, mtime, last_offset, last_processed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        conversation.source,
                        str(source_path),
                        conversation.source_session_id,
                        file_hash,
                        size,
                        mtime,
                        size,
                    ),
                )

    def import_state(self, source: str, source_path: Path) -> sqlite3.Row | None:
        self.initialize()
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM imports WHERE source = ? AND source_path = ?",
                (source, str(source_path)),
            ).fetchone()

    def search(
        self,
        query: str,
        source: str | None = None,
        project_id: str | None = None,
        limit: int = 10,
        date_from: str | None = None,
        date_to: str | None = None,
        offset: int = 0,
        device: str | None = None,
        current_device_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self.initialize()
        sql = f"""
            SELECT
                c.id,
                c.source,
                c.source_session_id,
                c.project_id,
                c.title,
                c.created_at,
                c.updated_at,
                c.model,
                c.archive_path,
                c.device_id,
                d.name AS device_name,
                p.name AS project_name,
                p.cwd AS project_path,
                {NAME_SQL},
                (SELECT substr(m.content, 1, 400) FROM messages m
                 WHERE m.conversation_id = c.id AND m.role = 'user'
                 ORDER BY m.ordinal DESC LIMIT 1) AS latest_user_message,
                substr(snippet(conversations_fts, 4, '[', ']', '...', 24), 1, 600) AS snippet,
                bm25(conversations_fts) AS score
            FROM conversations_fts
            JOIN conversations c ON c.id = conversations_fts.conversation_id
            JOIN devices d ON d.id = c.device_id
            LEFT JOIN projects p ON p.id = c.project_id
            WHERE conversations_fts MATCH ?
        """
        params: list[Any] = [_fts_query(query)]
        clause, filters = _filters(source, project_id, date_from, date_to, "c.")
        device_clause, device_filters = _device_filter(device, current_device_id, "c.")
        sql += clause + device_clause + " ORDER BY (p.name = ? COLLATE NOCASE) DESC, score, c.updated_at DESC, c.id LIMIT ? OFFSET ?"
        filters.extend([*device_filters, query.strip()])
        params.extend([*filters, max(1, min(limit, 1000)), max(0, offset)])
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def list_conversations(
        self,
        source: str | None = None,
        project_id: str | None = None,
        limit: int = 50,
        date_from: str | None = None,
        date_to: str | None = None,
        offset: int = 0,
        device: str | None = None,
        current_device_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self.initialize()
        sql = "SELECT * FROM conversations WHERE 1=1"
        params: list[Any] = []
        clause, params = _filters(source, project_id, date_from, date_to)
        sql += clause
        device_clause, device_filters = _device_filter(device, current_device_id)
        sql += device_clause
        params.extend(device_filters)
        sql = f"""
            SELECT c.*, d.name AS device_name, p.name AS project_name, p.cwd AS project_path, {NAME_SQL},
                (
                    SELECT substr(m.content, 1, 1000)
                    FROM messages m
                    WHERE m.conversation_id = c.id AND m.role = 'user'
                    ORDER BY m.ordinal DESC
                    LIMIT 1
                ) AS latest_user_message,
                (
                    SELECT m.timestamp
                    FROM messages m
                    WHERE m.conversation_id = c.id AND m.role = 'user'
                    ORDER BY m.ordinal DESC
                    LIMIT 1
                ) AS latest_user_message_at
            FROM ({sql}) c
            JOIN devices d ON d.id = c.device_id
            LEFT JOIN projects p ON p.id = c.project_id
            ORDER BY COALESCE(c.updated_at, c.created_at) DESC, c.id
            LIMIT ? OFFSET ?
        """
        params.extend([max(1, limit), max(0, offset)])
        with self.connect() as conn:
            rows = []
            for row in conn.execute(sql, params).fetchall():
                item = dict(row)
                item["latest_user_message"] = _conversation_preview(item.get("latest_user_message"))
                rows.append(item)
            return rows

    def list_devices(self, current_device_id: str) -> list[dict]:
        self.initialize()
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT d.id, d.name, (d.id = ? OR EXISTS(SELECT 1 FROM local_conversations lc "
                "JOIN conversations local ON local.id=lc.conversation_id WHERE local.device_id=d.id)) AS is_current, COUNT(c.id) AS conversation_count, "
                "MAX(c.updated_at) AS latest_conversation_at FROM devices d "
                "JOIN conversations c ON c.device_id = d.id GROUP BY d.id ORDER BY d.name, d.id",
                (current_device_id,))]

    def mark_local_conversations(self, conversation_ids) -> None:
        self.initialize()
        with self.connect() as conn:
            known = {row[0] for row in conn.execute("SELECT conversation_id FROM local_conversations")}
            conn.executemany("INSERT OR IGNORE INTO local_conversations VALUES (?)",
                             ((cid,) for cid in set(conversation_ids) - known if cid))

    def conversation_page(self, conversation_id: str, offset: int = 0, limit: int = 20,
                          latest: bool = False, include_tools: bool = False,
                          max_chars: int = 2000, include_context: bool = False) -> dict | None:
        """Read bounded indexed excerpts; never decompress a full session for MCP."""
        self.initialize()
        offset, limit, max_chars = max(0, offset), max(1, min(limit, 20)), max(100, min(max_chars, 4000))
        max_chars = min(max_chars, 48000 // (limit * (3 if include_tools else 1)))
        role_filter = "" if include_context else " AND role IN ('user', 'assistant')"
        with self.connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT c.id, c.source, c.source_session_id, c.title, c.created_at, c.updated_at, "
                f"c.device_id, d.name AS device_name, c.project_id, p.name AS project_name, p.cwd AS project_path, {NAME_SQL} "
                "FROM conversations c JOIN devices d ON d.id=c.device_id "
                "LEFT JOIN projects p ON p.id=c.project_id WHERE c.id=?", (conversation_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            count = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?" + role_filter,
                                 (conversation_id,)).fetchone()[0]
            start = max(0, count - offset - limit) if latest else offset
            size = min(limit, max(0, count - offset))
            result["messages"] = [dict(item) for item in conn.execute(
                "SELECT id, role, timestamp, ordinal, substr(content, 1, ?) AS content, "
                "length(content) > ? AS truncated FROM messages WHERE conversation_id=?" + role_filter +
                " ORDER BY ordinal, id LIMIT ? OFFSET ?", (max_chars, max_chars, conversation_id, size, start))]
            tool_count = conn.execute("SELECT COUNT(*) FROM tool_calls WHERE conversation_id=?",
                                      (conversation_id,)).fetchone()[0]
            result["tool_calls"] = []
            if include_tools:
                tool_start = max(0, tool_count - offset - limit) if latest else offset
                result["tool_calls"] = [dict(item) for item in conn.execute(
                    "SELECT id, name, timestamp, ordinal, substr(unpack_text(arguments),1,?) AS arguments, "
                    "substr(unpack_text(output),1,?) AS output, (COALESCE(length(unpack_text(arguments)),0)>? OR COALESCE(length(unpack_text(output)),0)>?) "
                    "AS truncated FROM tool_calls WHERE conversation_id=? ORDER BY ordinal, id LIMIT ? OFFSET ?",
                    (max_chars, max_chars, max_chars, max_chars, conversation_id,
                     min(limit, max(0, tool_count-offset)), tool_start))]
            total = max(count, tool_count) if include_tools else count
            result.update(message_count=count, tool_call_count=tool_count, offset=offset,
                          limit=limit, latest=latest, next_offset=offset+limit if offset+limit < total else None)
            return result

    def list_projects(self) -> list[dict]:
        self.initialize()
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT p.*, COUNT(c.id) AS conversation_count, "
                "MAX(COALESCE(c.updated_at, c.created_at)) AS latest_conversation_at FROM projects p "
                "JOIN conversations c ON c.project_id = p.id GROUP BY p.id ORDER BY p.name, p.id"
            )]

    def message_excerpt(self, conversation_id: str, message_id: str, offset: int = 0,
                        limit: int = 8000, field: str = "content") -> dict | None:
        self.initialize()
        if field not in {"content", "output", "arguments"}:
            raise ValueError("field must be content, output or arguments")
        table = "messages" if field == "content" else "tool_calls"
        expression = field if field == "content" else f"unpack_text({field})"
        offset, limit = max(0, offset), max(1, min(limit, 16000))
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT id, substr({expression}, ?, ?) AS text, length({expression}) AS total_chars "
                f"FROM {table} WHERE conversation_id=? AND id=?",
                (offset+1, limit, conversation_id, message_id)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result.update(field=field, offset=offset,
                          next_offset=offset+limit if offset+limit < (result["total_chars"] or 0) else None)
            return result

    def archive_entries(self) -> list[dict]:
        self.initialize()
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT id, source, archive_path FROM conversations")]

    def pending_name_entries(self, limit: int = 20) -> list[dict]:
        """Conversations indexed before names were recorded (Codex names come from Codex)."""
        self.initialize()
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT c.id, c.archive_path FROM conversations c LEFT JOIN conversation_names n "
                f"ON n.conversation_id = c.id WHERE n.conversation_id IS NULL AND c.source NOT IN "
                f"({', '.join('?' * len(CODEX_SOURCES))}) ORDER BY c.id LIMIT ?", (*sorted(CODEX_SOURCES), limit))]

    def record_title_name(self, conversation: NormalizedConversation) -> None:
        self.initialize()
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO conversation_names VALUES (?, ?, 'title')",
                         (conversation.id, explicit_title(conversation)))

    def update_codex_names(self, names: dict[str, str]) -> int:
        """Store renamed Codex threads; only changed rows are written."""
        self.initialize()
        with self.connect() as conn:
            known = {row[0]: row[1] for row in conn.execute(
                "SELECT conversation_id, name FROM conversation_names WHERE origin = 'codex'")}
            changed = [(cid, name) for cid, name in names.items() if known.get(cid) != name]
            conn.executemany("INSERT OR REPLACE INTO conversation_names VALUES (?, ?, 'codex')", changed)
        return len(changed)

    def apply_catalog(self, record: dict, digest: str) -> bool:
        from aimemory.catalog import validate_record
        validate_record(record)
        self.initialize()
        cid, field = record["conversation_id"], record["field"]
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT updated_at, digest FROM conversation_metadata WHERE conversation_id=? AND field=?",
                                    (cid, field)).fetchone()
            if previous and tuple(previous) >= (record["updated_at"], digest):
                return False
            conn.execute("INSERT OR REPLACE INTO conversation_metadata VALUES (?, ?, ?, ?, ?)",
                         (cid, field, json.dumps(record["value"], ensure_ascii=False), record["updated_at"], digest))
            self._apply_catalog_to_index(conn, cid)
        return True

    def _apply_catalog_to_index(self, conn, cid: str) -> None:
        records = conn.execute("SELECT field, value FROM conversation_metadata WHERE conversation_id=?", (cid,)).fetchall()
        if not records:
            return
        values = {row[0]: json.loads(row[1]) for row in records}
        if "name" in values:
            conn.execute("INSERT OR REPLACE INTO conversation_names VALUES (?, ?, 'catalog')", (cid, values["name"]))
            conn.execute("UPDATE conversations SET title=? WHERE id=?", (values["name"], cid))
        if "project" in values:
            project = values["project"]
            newest = conn.execute("SELECT value FROM conversation_metadata WHERE field='project' "
                                  "AND json_extract(value, '$.id')=? ORDER BY updated_at DESC, digest DESC LIMIT 1",
                                  (project["id"],)).fetchone()
            if newest:
                project = json.loads(newest[0])
            previous_project = conn.execute("SELECT name, cwd FROM projects WHERE id=?", (project["id"],)).fetchone()
            conn.execute("INSERT INTO projects(id,name,cwd,git_remote_normalized,default_branch) VALUES (?,?,?,?,?) "
                         "ON CONFLICT(id) DO UPDATE SET name=excluded.name,cwd=excluded.cwd,git_remote_normalized=excluded.git_remote_normalized,default_branch=excluded.default_branch",
                         (project["id"], project["name"], project.get("cwd"), project.get("git_remote"), project.get("git_branch")))
            conn.execute("UPDATE conversations SET project_id=? WHERE id=?", (project["id"], cid))
            if previous_project and tuple(previous_project) != (project["name"], project.get("cwd")):
                for sibling in conn.execute("SELECT c.id, COALESCE(n.name,c.title) FROM conversations c "
                                            "LEFT JOIN conversation_names n ON n.conversation_id=c.id WHERE c.project_id=?", (project["id"],)).fetchall():
                    title = "\n".join(value for value in (sibling[1], project["name"], project.get("cwd")) if value)
                    conn.execute("UPDATE conversations_fts SET title=? WHERE rowid IN "
                                 "(SELECT fts_rowid FROM fts_conversation_rows WHERE conversation_id=?)", (title, sibling[0]))
        row = conn.execute("SELECT c.title, c.project_id, p.name, p.cwd FROM conversations c LEFT JOIN projects p ON p.id=c.project_id WHERE c.id=?", (cid,)).fetchone()
        if row:
            title = "\n".join(str(value) for value in (values.get("name") or row[0], row[2], row[3]) if value)
            conn.execute("UPDATE conversations_fts SET title=?, project_id=? WHERE rowid IN "
                         "(SELECT fts_rowid FROM fts_conversation_rows WHERE conversation_id=?)", (title, row[1], cid))

    def pending_compact_entries(self, limit: int = 3) -> list[dict]:
        self.initialize()
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT c.id, c.archive_path FROM conversations c LEFT JOIN compact_index_versions v "
                "ON v.conversation_id=c.id WHERE COALESCE(v.version,0)<1 ORDER BY c.id LIMIT ?", (limit,))]

    def update_source(self, conversation_id: str, source: str) -> None:
        self.initialize()
        with self.connect() as conn:
            conn.execute("UPDATE conversations SET source = ? WHERE id = ?", (source, conversation_id))
            conn.execute("UPDATE conversations_fts SET source = ? WHERE rowid IN (SELECT fts_rowid FROM fts_conversation_rows WHERE conversation_id=?)", (source, conversation_id))

    def get_conversation_row(self, conversation_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            return dict(row) if row else None

    def status(self) -> dict[str, Any]:
        self.initialize()
        with self.connect() as conn:
            conversations = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            imports = conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
            projects = conn.execute("SELECT COUNT(DISTINCT project_id) FROM conversations").fetchone()[0]
        return {
            "database": str(self.path),
            "conversation_count": conversations,
            "import_count": imports,
            "project_count": projects,
        }


def explicit_title(conversation: NormalizedConversation) -> str | None:
    """A title is a name only when it is not just copied from a user message."""
    title = " ".join((conversation.title or "").split())
    if not title or title == title_from_messages(conversation.messages):
        return None
    for message in conversation.messages:
        if message.role == "user" and " ".join(message.content.split()).startswith(title):
            return None
    return title


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _filters(source, project, date_from, date_to, prefix=""):
    clauses, values = [], []
    if source and source != "all":
        sources = {
            "claude": ("claude", "claude-code", "claude-desktop", "vscode-claude"),
            "codex": ("codex", "codex-desktop", "vscode-codex"),
            "vscode": ("vscode", "vscode-codex", "vscode-claude"),
        }.get(source, (source,))
        clauses.append(f"{prefix}source IN ({','.join('?' for _ in sources)})")
        values.extend(sources)
    if project:
        clauses.append(f"{prefix}project_id IN (SELECT id FROM projects WHERE id = ? OR name = ? COLLATE NOCASE OR cwd = ? OR git_remote_normalized = ?)")
        values.extend([project] * 4)
    for value, operator, end in ((date_from, ">=", False), (date_to, "<=", True)):
        if value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if end and len(value) == 10:
                parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            clauses.append(f"julianday(COALESCE({prefix}updated_at, {prefix}created_at)) {operator} julianday(?)")
            values.append(parsed.isoformat())
    return "".join(" AND " + clause for clause in clauses), values


def _scoped_id(conversation_id: str, raw_id: str) -> str:
    return hashlib.sha256(f"{conversation_id}:{raw_id}".encode("utf-8")).hexdigest()


def _device_filter(device, current_device_id, prefix=""):
    if not device or device == "all":
        return "", []
    if device in {"other", "current"}:
        if not current_device_id:
            raise ValueError("Current device identity is required")
        expression = f"({prefix}device_id = ? OR {prefix}id IN (SELECT conversation_id FROM local_conversations))"
        return f" AND {'NOT ' if device == 'other' else ''}{expression}", [current_device_id]
    return (f" AND {prefix}device_id IN (SELECT id FROM devices WHERE id=? OR name=? COLLATE NOCASE)",
            [device, device])


def _fts_query(query: str) -> str:
    terms = [term.strip() for term in query.replace('"', " ").split() if term.strip()]
    if not terms:
        return '""'
    return " OR ".join(f'"{term}"' for term in terms)


def _conversation_preview(content: str | None, limit: int = 240) -> str | None:
    if not content:
        return None
    text = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    marker = "## My request:"
    if marker in text:
        text = text.split(marker, 1)[1].strip()
    text = " ".join(text.split())
    if not text:
        return None
    return text[: limit - 1].rstrip() + "…" if len(text) > limit else text
