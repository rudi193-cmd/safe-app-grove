"""
grove_db.py -- Grove workspace messaging database using the 23-cubed lattice structure.

PostgreSQL-only. Schema: grove.
Each entity maps into a 23x23x23 lattice (12,167 cells per entity).

Lattice constants imported from Willow's user_lattice.py.
DB connection follows Willow's core/db.py pattern (psycopg2, pooled).
"""

import os
import sys
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

# Import 23-cubed lattice constants from Willow
sys.path.insert(0, "/mnt/c/Users/Sean/Documents/GitHub/Willow/core")
from user_lattice import DOMAINS, TEMPORAL_STATES, DEPTH_MIN, DEPTH_MAX, LATTICE_SIZE

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

_pool = None
_pool_lock = threading.Lock()

SCHEMA = "grove"

VALID_CHANNEL_TYPES = frozenset({"direct", "group", "persona", "broadcast"})
VALID_MESSAGE_TYPES = frozenset({"text", "system", "file_share", "reaction"})


def _resolve_host() -> str:
    """Return localhost, falling back to WSL resolv.conf nameserver."""
    host = "localhost"
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                if line.strip().startswith("nameserver"):
                    host = line.strip().split()[1]
                    break
    except FileNotFoundError:
        pass
    return host


def _get_pool():
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            import psycopg2.pool
            dsn = os.getenv("WILLOW_DB_URL", "")
            if not dsn:
                host = _resolve_host()
                dsn = f"dbname=willow user=willow host={host}"
            _pool = psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=10, dsn=dsn)
    return _pool


def get_connection():
    """Return a pooled Postgres connection with search_path = grove, public."""
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
    """Return a connection to the pool."""
    try:
        conn.rollback()
    except Exception:
        pass
    _get_pool().putconn(conn)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_lattice(domain: str, depth: int, temporal: str):
    if domain not in DOMAINS:
        raise ValueError(f"Invalid domain '{domain}'. Must be one of: {DOMAINS}")
    if not (DEPTH_MIN <= depth <= DEPTH_MAX):
        raise ValueError(f"Invalid depth {depth}. Must be {DEPTH_MIN}-{DEPTH_MAX}")
    if temporal not in TEMPORAL_STATES:
        raise ValueError(f"Invalid temporal '{temporal}'. Must be one of: {TEMPORAL_STATES}")


# ---------------------------------------------------------------------------
# Schema init
# ---------------------------------------------------------------------------

def init_schema(conn):
    """Create the grove schema and all tables. Idempotent."""
    cur = conn.cursor()

    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    cur.execute(f"SET search_path = {SCHEMA}, public")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            name            TEXT NOT NULL,
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
            message_type        TEXT NOT NULL CHECK (message_type IN ('text','system','file_share','reaction')),
            reply_to_id         INTEGER,
            willow_indexed_at   TIMESTAMP,
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_deleted          INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS lattice_cells (
            id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            entity_id       BIGINT NOT NULL,
            entity_type     TEXT NOT NULL CHECK (entity_type IN ('channel','message')),
            domain          TEXT NOT NULL,
            depth           INTEGER NOT NULL CHECK (depth >= 1 AND depth <= 23),
            temporal        TEXT NOT NULL,
            content         TEXT NOT NULL,
            source          TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_sensitive    BOOLEAN DEFAULT FALSE,
            UNIQUE(entity_id, entity_type, domain, depth, temporal)
        )
    """)

    # Indices
    cur.execute("CREATE INDEX IF NOT EXISTS idx_channels_name ON channels (name)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_channels_type ON channels (channel_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages (channel_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages (sender)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_created ON messages (created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lc_entity ON lattice_cells (entity_id, entity_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lc_domain ON lattice_cells (domain)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lc_temporal ON lattice_cells (temporal)")

    conn.commit()


# ---------------------------------------------------------------------------
# CRUD -- all return new dicts (immutable pattern)
# ---------------------------------------------------------------------------

def add_channel(conn, *, name: str, channel_type: str, description: str = None) -> Dict[str, Any]:
    """Insert a channel. Returns a dict with the new row (including id)."""
    if channel_type not in VALID_CHANNEL_TYPES:
        raise ValueError(f"Invalid channel_type '{channel_type}'. Must be one of: {VALID_CHANNEL_TYPES}")
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


def add_message(conn, *, channel_id: int, sender: str, content: str,
                message_type: str = "text", reply_to_id: int = None) -> Dict[str, Any]:
    """Insert a message into a channel. Returns the new row as a dict."""
    if message_type not in VALID_MESSAGE_TYPES:
        raise ValueError(f"Invalid message_type '{message_type}'. Must be one of: {VALID_MESSAGE_TYPES}")
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


def place_in_lattice(conn, entity_id: int, entity_type: str, domain: str, depth: int,
                     temporal: str, content: str, source: str = None,
                     is_sensitive: bool = False) -> Dict[str, Any]:
    """Map an entity to a lattice cell. Upserts on (entity_id, entity_type, domain, depth, temporal).
    Returns the cell row as a dict."""
    if entity_type not in ("channel", "message"):
        raise ValueError(f"Invalid entity_type '{entity_type}'. Must be 'channel' or 'message'")
    _validate_lattice(domain, depth, temporal)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO lattice_cells (entity_id, entity_type, domain, depth, temporal, content, source, is_sensitive)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (entity_id, entity_type, domain, depth, temporal)
        DO UPDATE SET content = EXCLUDED.content, source = EXCLUDED.source, is_sensitive = EXCLUDED.is_sensitive
        RETURNING id, entity_id, entity_type, domain, depth, temporal, content, source, created_at, is_sensitive
    """, (entity_id, entity_type, domain, depth, temporal, content, source, is_sensitive))
    row = cur.fetchone()
    cols = [d[0] for d in cur.description]
    conn.commit()
    return dict(zip(cols, row))


def get_channel_history(conn, channel_id: int, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    """Return messages for a channel, newest first. Immutable result."""
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM messages
        WHERE channel_id = %s AND is_deleted = 0
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
    """, (channel_id, limit, offset))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def search_messages(conn, query: str, channel_id: int = None) -> List[Dict[str, Any]]:
    """Search messages by content (case-insensitive ILIKE). Optionally filter by channel.
    Returns list of dicts."""
    cur = conn.cursor()
    if channel_id is not None:
        cur.execute("""
            SELECT * FROM messages
            WHERE content ILIKE %s AND channel_id = %s AND is_deleted = 0
            ORDER BY created_at DESC
        """, (f"%{query}%", channel_id))
    else:
        cur.execute("""
            SELECT * FROM messages
            WHERE content ILIKE %s AND is_deleted = 0
            ORDER BY created_at DESC
        """, (f"%{query}%",))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def get_unindexed_messages(conn, limit: int = 100) -> List[Dict[str, Any]]:
    """Return messages not yet indexed by Willow. Immutable result."""
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM messages
        WHERE willow_indexed_at IS NULL AND is_deleted = 0
        ORDER BY created_at ASC
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]
