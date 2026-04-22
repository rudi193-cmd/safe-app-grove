"""
SAFE Framework Integration — Grove
====================================
Grove's connection to the Willow 1.9 knowledge bus.
b17: GRSI9  ΔΣ=42

Portless. Reads/writes directly via the shared willow_19 Postgres DB.
No HTTP. No porch. No queue files.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

APP_ID = "grove"
_APP_DATA = Path.home() / ".willow" / "apps" / APP_ID


def _get_bridge():
    """Load willow-1.9 PgBridge from repo path."""
    import importlib.util
    willow_root = Path(os.environ.get("WILLOW_ROOT",
                       Path.home() / "github" / "willow-1.9"))
    spec = importlib.util.spec_from_file_location(
        "pg_bridge_19", Path(willow_root) / "core" / "pg_bridge.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PgBridge()


def query(q: str, limit: int = 5) -> list:
    """Query willow-1.9 knowledge store via Postgres."""
    try:
        bridge = _get_bridge()
        return bridge.knowledge_search(q, project=APP_ID, limit=limit)
    except Exception:
        return []


def contribute(content: str, category: str = "narrative",
               metadata: Optional[dict] = None) -> dict:
    """Write a knowledge atom to willow-1.9 KB from Grove."""
    try:
        bridge = _get_bridge()
        atom_id = f"grove_{uuid.uuid4().hex[:8]}"
        bridge.knowledge_put({
            "id": atom_id,
            "project": APP_ID,
            "title": content[:120],
            "summary": content[:500],
            "source_type": "grove_contribution",
            "category": category,
            "content": metadata or {},
        })
        return {"ok": True, "id": atom_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def flush_to_kb() -> int:
    """
    Index unread Grove messages into the willow-1.9 knowledge store.
    Reads from grove.messages (willow_19 DB), writes to public.knowledge.
    Marks indexed rows with willow_indexed_at = NOW().
    Returns count of messages indexed.
    """
    try:
        bridge = _get_bridge()
    except Exception:
        return 0

    try:
        import psycopg2.extras
        with bridge.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT m.id, m.sender, m.content, m.created_at, c.name AS channel_name
                FROM grove.messages m
                JOIN grove.channels c ON m.channel_id = c.id
                WHERE m.willow_indexed_at IS NULL AND m.is_deleted = 0
                ORDER BY m.created_at ASC LIMIT 50
            """)
            messages = [dict(r) for r in cur.fetchall()]
    except Exception:
        return 0

    if not messages:
        return 0

    indexed_ids = []
    for msg in messages:
        # DM channels are prefixed dm: — use sender name as project
        ch = msg["channel_name"]
        project = f"grove_{ch.replace(':', '_').replace('@', '_')}"
        sender_short = msg["sender"].split("@")[0]
        bridge.knowledge_put({
            "id": f"grove_msg_{msg['id']}",
            "project": project,
            "title": f"[{ch}] {sender_short}",
            "summary": (msg["content"] or "")[:500],
            "source_type": "grove_message",
            "category": "message",
            "valid_at": msg["created_at"],
            "content": {
                "sender": msg["sender"],
                "channel": ch,
                "grove_message_id": msg["id"],
            },
        })
        indexed_ids.append(msg["id"])

    try:
        with bridge.conn.cursor() as cur:
            cur.execute(
                "UPDATE grove.messages SET willow_indexed_at = NOW() WHERE id = ANY(%s)",
                (indexed_ids,)
            )
        bridge.conn.commit()
    except Exception:
        pass

    return len(indexed_ids)


def status() -> dict:
    """Check if willow-1.9 KB is reachable."""
    try:
        bridge = _get_bridge()
        bridge.conn.close()
        return {"ok": True, "mode": "postgres", "db": os.getenv("WILLOW_PG_DB", "willow_19")}
    except Exception as e:
        return {"ok": False, "error": str(e), "mode": "postgres"}


def connect(entity_a: str, entity_b: str, relation: str = "related_to") -> dict:
    """Propose an entity connection for Willow review."""
    return contribute(
        f"{entity_a} {relation} {entity_b}",
        category="connection",
        metadata={"entity_a": entity_a, "entity_b": entity_b, "relation": relation},
    )
