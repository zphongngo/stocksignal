import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _connect(database_path):
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            chat_id TEXT PRIMARY KEY,
            username TEXT,
            display_name TEXT,
            last_message_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            direction TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.commit()
    return connection


def record_message(
    database_path,
    chat_id,
    direction,
    text,
    username=None,
    display_name=None,
    created_at=None,
):
    if not text:
        return
    timestamp = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    connection = None
    try:
        connection = _connect(database_path)
        with connection:
            connection.execute(
                """
                INSERT INTO conversations (chat_id, username, display_name, last_message_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    username = COALESCE(excluded.username, conversations.username),
                    display_name = COALESCE(excluded.display_name, conversations.display_name),
                    last_message_at = excluded.last_message_at
                """,
                (str(chat_id), username, display_name, timestamp),
            )
            connection.execute(
                "INSERT INTO messages (chat_id, direction, text, created_at) VALUES (?, ?, ?, ?)",
                (str(chat_id), direction, text, timestamp),
            )
            connection.execute(
                "DELETE FROM messages WHERE id NOT IN (SELECT id FROM messages ORDER BY id DESC LIMIT 10000)"
            )
    except sqlite3.Error:
        logging.exception("Could not save Telegram inbox message")
    finally:
        if connection is not None:
            connection.close()


def list_conversations(database_path):
    connection = None
    try:
        connection = _connect(database_path)
        rows = connection.execute(
            "SELECT chat_id, username, display_name, last_message_at "
            "FROM conversations ORDER BY last_message_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        logging.exception("Could not load Telegram inbox conversations")
        return []
    finally:
        if connection is not None:
            connection.close()


def list_messages(database_path, chat_id, limit=200):
    connection = None
    try:
        connection = _connect(database_path)
        rows = connection.execute(
            """
            SELECT direction, text, created_at FROM (
                SELECT id, direction, text, created_at
                FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC
            """,
            (str(chat_id), limit),
        ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        logging.exception("Could not load Telegram inbox messages")
        return []
    finally:
        if connection is not None:
            connection.close()