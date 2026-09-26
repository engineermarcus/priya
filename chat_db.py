"""
chat_db.py - SQLite storage for Priya chat conversations tied to project directories.
"""

import os
import sqlite3
import threading
import time
import json
import uuid
from pathlib import Path
from typing import List, Dict, Optional, Any


DEFAULT_DB_DIR = Path.home() / ".priya"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "chats.db"


class ChatDB:
    def __init__(self, db_path: Optional[str | Path] = None):
        if db_path is None:
            DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)
            self.db_path = str(DEFAULT_DB_PATH)
        else:
            p = Path(db_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            self.db_path = str(p)

        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._get_conn()
            try:
                conn.executescript("""
                CREATE TABLE IF NOT EXISTS chats (
                    id TEXT PRIMARY KEY,
                    project_dir TEXT NOT NULL,
                    title TEXT NOT NULL,
                    model TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_chats_project ON chats(project_dir);
                CREATE INDEX IF NOT EXISTS idx_chats_updated ON chats(updated_at DESC);

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    sender TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id);
                """)
                conn.commit()
            finally:
                conn.close()

    def create_chat(
        self,
        project_dir: str,
        title: str = "New Chat",
        model: str = "mistral-medium-latest"
    ) -> str:
        """Create a new chat session tied to a project directory."""
        chat_id = f"chat_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        now = time.time()
        norm_dir = os.path.abspath(project_dir)

        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT INTO chats (id, project_dir, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (chat_id, norm_dir, title, model, now, now)
                )
                conn.commit()
            finally:
                conn.close()
        return chat_id

    def add_message(
        self,
        chat_id: str,
        sender: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """Add a message to a chat. Updates updated_at and first prompt title."""
        now = time.time()
        meta_str = json.dumps(metadata) if metadata else None

        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO messages (chat_id, sender, content, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
                    (chat_id, sender, content, meta_str, now)
                )
                msg_id = cur.lastrowid

                # Update chat updated_at
                cur.execute(
                    "UPDATE chats SET updated_at = ? WHERE id = ?",
                    (now, chat_id)
                )

                # If title is still 'New Chat' and user speaks, update title from first message
                if sender.lower() == "user":
                    cur.execute("SELECT title FROM chats WHERE id = ?", (chat_id,))
                    row = cur.fetchone()
                    if row and row["title"] == "New Chat":
                        first_line = content.strip().split("\n")[0]
                        new_title = (first_line[:48] + "…") if len(first_line) > 50 else first_line
                        if new_title:
                            cur.execute(
                                "UPDATE chats SET title = ? WHERE id = ?",
                                (new_title, chat_id)
                            )

                conn.commit()
                return msg_id
            finally:
                conn.close()

    def get_chats(self, project_dir: Optional[str] = None) -> List[Dict[str, Any]]:
        """List chats for a specific project directory (or all if None), ordered newest first."""
        with self._lock:
            conn = self._get_conn()
            try:
                if project_dir is not None:
                    norm_dir = os.path.abspath(project_dir)
                    query = """
                    SELECT c.id, c.project_dir, c.title, c.model, c.created_at, c.updated_at,
                           COUNT(m.id) as message_count
                    FROM chats c
                    LEFT JOIN messages m ON c.id = m.chat_id
                    WHERE c.project_dir = ?
                    GROUP BY c.id
                    ORDER BY c.updated_at DESC
                    """
                    cur = conn.execute(query, (norm_dir,))
                else:
                    query = """
                    SELECT c.id, c.project_dir, c.title, c.model, c.created_at, c.updated_at,
                           COUNT(m.id) as message_count
                    FROM chats c
                    LEFT JOIN messages m ON c.id = m.chat_id
                    GROUP BY c.id
                    ORDER BY c.updated_at DESC
                    """
                    cur = conn.execute(query)

                rows = cur.fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_messages(self, chat_id: str) -> List[Dict[str, Any]]:
        """Retrieve all messages for a chat in chronological order."""
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "SELECT id, chat_id, sender, content, metadata, created_at FROM messages WHERE chat_id = ? ORDER BY id ASC",
                    (chat_id,)
                )
                rows = cur.fetchall()
                out = []
                for r in rows:
                    d = dict(r)
                    if d["metadata"]:
                        try:
                            d["metadata"] = json.loads(d["metadata"])
                        except Exception:
                            pass
                    out.append(d)
                return out
            finally:
                conn.close()

    def delete_chat(self, chat_id: str) -> bool:
        """Delete a chat and its messages from SQLite."""
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def get_chat(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """Get single chat metadata."""
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,))
                row = cur.fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def update_chat_model(self, chat_id: str, model: str) -> bool:
        """Update active model for a chat."""
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute("UPDATE chats SET model = ?, updated_at = ? WHERE id = ?", (model, time.time(), chat_id))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()
