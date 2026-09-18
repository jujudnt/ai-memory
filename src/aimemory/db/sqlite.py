from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from aimemory.models import NormalizedConversation


class MemoryDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
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

                CREATE INDEX IF NOT EXISTS idx_messages_conversation_role_ordinal
                ON messages(conversation_id, role, ordinal DESC);

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

                CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(
                    conversation_id UNINDEXED,
                    source UNINDEXED,
                    project_id UNINDEXED,
                    title,
                    content
                );
                """
            )

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
            if conversation.project:
                project_id = conversation.project.id
                conn.execute(
                    """
                    INSERT OR REPLACE INTO projects(
                        id, name, cwd, git_remote_normalized, default_branch, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM projects WHERE id = ?), CURRENT_TIMESTAMP), CURRENT_TIMESTAMP)
                    """,
                    (
                        conversation.project.id,
                        conversation.project.name,
                        conversation.project.cwd,
                        conversation.project.git_remote,
                        conversation.project.git_branch,
                        conversation.project.id,
                    ),
                )

            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation.id,))
            conn.execute("DELETE FROM tool_calls WHERE conversation_id = ?", (conversation.id,))
            conn.execute("DELETE FROM conversations_fts WHERE conversation_id = ?", (conversation.id,))
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
                        call.arguments,
                        call.output,
                        call.timestamp,
                        call.ordinal,
                    ),
                )
            conn.execute(
                """
                INSERT INTO conversations_fts(conversation_id, source, project_id, title, content)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    conversation.id,
                    conversation.source,
                    project_id,
                    conversation.title or "",
                    conversation.searchable_text(),
                ),
            )

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
    ) -> list[dict[str, Any]]:
        self.initialize()
        sql = """
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
                snippet(conversations_fts, 4, '[', ']', '...', 24) AS snippet,
                bm25(conversations_fts) AS score
            FROM conversations_fts
            JOIN conversations c ON c.id = conversations_fts.conversation_id
            WHERE conversations_fts MATCH ?
        """
        params: list[Any] = [_fts_query(query)]
        if source:
            sql += " AND c.source = ?"
            params.append(source)
        if project_id:
            sql += " AND c.project_id = ?"
            params.append(project_id)
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def list_conversations(
        self,
        source: str | None = None,
        project_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        self.initialize()
        sql = "SELECT * FROM conversations WHERE 1=1"
        params: list[Any] = []
        if source:
            sql += " AND source = ?"
            params.append(source)
        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)
        sql = f"""
            SELECT c.*,
                (
                    SELECT m.content
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
            ORDER BY COALESCE(c.updated_at, c.created_at) DESC
            LIMIT ?
        """
        params.append(limit)
        with self.connect() as conn:
            rows = []
            for row in conn.execute(sql, params).fetchall():
                item = dict(row)
                item["latest_user_message"] = _conversation_preview(item.get("latest_user_message"))
                rows.append(item)
            return rows

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
            projects = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        return {
            "database": str(self.path),
            "conversation_count": conversations,
            "import_count": imports,
            "project_count": projects,
        }


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scoped_id(conversation_id: str, raw_id: str) -> str:
    return hashlib.sha256(f"{conversation_id}:{raw_id}".encode("utf-8")).hexdigest()


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
