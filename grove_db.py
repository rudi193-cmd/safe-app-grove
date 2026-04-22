"""
grove_db.py — Grove workspace messaging database.

PostgreSQL, grove schema. Channels, messages, threads.
"""

import os
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any

_pool = None
_pool_lock = threading.Lock()

SCHEMA = "grove"

VALID_CHANNEL_TYPES = frozenset({"direct", "group", "persona", "broadcast"})
VALID_MESSAGE_TYPES = frozenset({"text", "system", "file_share", "reaction"})


def _get_pool():
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            import psycopg2.pool
            dsn = os.getenv("WILLOW_DB_URL", "")
            if not dsn:
                dsn = f"dbname={os.getenv('WILLOW_PG_DB', 'willow')} user={os.getenv('WILLOW_PG_USER', 'sean-campbell')}"
            _pool = psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=10, dsn=dsn)
    return _pool


def get_connection():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute(f"SET search_path = {SCHEMA}, public")
        cur.close()
        return conn
    except Exception:
        pool.putconn(conn)
        raise


def release_connection(conn):
    try:
        conn.rollback()
    except Exception:
        pass
    _get_pool().putconn(conn)


def init_schema(conn):
    """Create grove schema and tables. Idempotent."""
    cur = conn.cursor()
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    cur.execute(f"SET search_path = {SCHEMA}, public")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            name            TEXT NOT NULL UNIQUE,
            channel_type    TEXT NOT NULL CHECK (channel_type IN ('direct','group','persona','broadcast')),
            description     TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_archived     BOOLEAN DEFAULT FALSE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            channel_id          BIGINT NOT NULL REFERENCES channels(id),
            sender              TEXT NOT NULL,
            content             TEXT NOT NULL,
            message_type        TEXT NOT NULL DEFAULT 'text'
                                    CHECK (message_type IN ('text','system','file_share','reaction')),
            reply_to_id         BIGINT REFERENCES messages(id),
            willow_indexed_at   TIMESTAMP,
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_deleted          INTEGER DEFAULT 0
        )
    """)

    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_channels_name ON channels (name)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_channels_type ON channels (channel_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages (channel_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages (sender)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_created ON messages (created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_reply ON messages (reply_to_id)")

    conn.commit()


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------

def create_channel(conn, *, name: str, channel_type: str, description: str = None) -> Dict[str, Any]:
    if channel_type not in VALID_CHANNEL_TYPES:
        raise ValueError(f"channel_type must be one of {VALID_CHANNEL_TYPES}")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO channels (name, channel_type, description)
        VALUES (%s, %s, %s)
        RETURNING id, name, channel_type, description, created_at, updated_at, is_archived
    """, (name, channel_type, description))
    row = cur.fetchone()
    cols = [d[0] for d in cur.description]
    conn.commit()
    return dict(zip(cols, row))


def list_channels(conn, include_archived: bool = False) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    if include_archived:
        cur.execute("SELECT * FROM channels ORDER BY name")
    else:
        cur.execute("SELECT * FROM channels WHERE is_archived = FALSE ORDER BY name")
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def get_channel(conn, channel_id: int) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM channels WHERE id = %s", (channel_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def archive_channel(conn, channel_id: int) -> bool:
    cur = conn.cursor()
    cur.execute(
        "UPDATE channels SET is_archived = TRUE, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
        (channel_id,)
    )
    conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def send_message(conn, *, channel_id: int, sender: str, content: str,
                 message_type: str = "text", reply_to_id: int = None) -> Dict[str, Any]:
    if message_type not in VALID_MESSAGE_TYPES:
        raise ValueError(f"message_type must be one of {VALID_MESSAGE_TYPES}")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO messages (channel_id, sender, content, message_type, reply_to_id)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id, channel_id, sender, content, message_type, reply_to_id,
                  willow_indexed_at, created_at, is_deleted
    """, (channel_id, sender, content, message_type, reply_to_id))
    row = cur.fetchone()
    cols = [d[0] for d in cur.description]
    conn.commit()
    return dict(zip(cols, row))


def get_history(conn, channel_id: int, limit: int = 100, before_id: int = None) -> List[Dict[str, Any]]:
    """Return top-level messages (no replies), newest first."""
    cur = conn.cursor()
    if before_id:
        cur.execute("""
            SELECT * FROM messages
            WHERE channel_id = %s AND reply_to_id IS NULL AND is_deleted = 0 AND id < %s
            ORDER BY created_at DESC LIMIT %s
        """, (channel_id, before_id, limit))
    else:
        cur.execute("""
            SELECT * FROM messages
            WHERE channel_id = %s AND reply_to_id IS NULL AND is_deleted = 0
            ORDER BY created_at DESC LIMIT %s
        """, (channel_id, limit))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def get_thread(conn, parent_id: int) -> List[Dict[str, Any]]:
    """Return all replies to a message, oldest first."""
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM messages
        WHERE reply_to_id = %s AND is_deleted = 0
        ORDER BY created_at ASC
    """, (parent_id,))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def delete_message(conn, message_id: int) -> bool:
    cur = conn.cursor()
    cur.execute("UPDATE messages SET is_deleted = 1 WHERE id = %s", (message_id,))
    conn.commit()
    return cur.rowcount > 0


def search_messages(conn, query: str, channel_id: int = None) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    if channel_id is not None:
        cur.execute("""
            SELECT * FROM messages
            WHERE content ILIKE %s AND channel_id = %s AND is_deleted = 0
            ORDER BY created_at DESC LIMIT 100
        """, (f"%{query}%", channel_id))
    else:
        cur.execute("""
            SELECT * FROM messages
            WHERE content ILIKE %s AND is_deleted = 0
            ORDER BY created_at DESC LIMIT 100
        """, (f"%{query}%",))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def get_unindexed(conn, limit: int = 100) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM messages
        WHERE willow_indexed_at IS NULL AND is_deleted = 0
        ORDER BY created_at ASC LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def mark_indexed(conn, message_ids: List[int]) -> int:
    if not message_ids:
        return 0
    cur = conn.cursor()
    cur.execute(
        "UPDATE messages SET willow_indexed_at = CURRENT_TIMESTAMP WHERE id = ANY(%s)",
        (message_ids,)
    )
    conn.commit()
    return cur.rowcount
