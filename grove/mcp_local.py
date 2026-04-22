# grove/mcp_local.py — Grove stdio MCP for Claude Code.
# b17: GRMLC  ΔΣ=42
"""
Local stdio transport — no OAuth, no tunnel, no port.
Run via .mcp.json:
  "grove": { "command": "python3", "args": ["-m", "grove.mcp_local"] }

Same tools as mcp_server.py. Auth is implicit (local process, trusted user).
"""
import asyncio
import os
import socket
from pathlib import Path

from mcp.server.fastmcp import FastMCP

import grove_db as db

mcp = FastMCP(
    "grove",
    instructions=(
        "Grove sovereign workspace messaging. "
        "Send and read messages, search conversations, list channels."
    ),
)


@mcp.tool()
def grove_list_channels() -> list[dict]:
    """List all active Grove channels (name, type, description)."""
    conn = db.get_connection()
    try:
        rows = db.list_channels(conn)
        return [
            {"id": r["id"], "name": r["name"], "type": r["channel_type"],
             "description": r.get("description")}
            for r in rows
        ]
    finally:
        db.release_connection(conn)


@mcp.tool()
def grove_get_history(channel_name: str, limit: int = 50, since_id: int = 0) -> list[dict]:
    """
    Get message history from a Grove channel.

    Args:
        channel_name: Exact channel name (use grove_list_channels to find names).
        limit: Number of messages to return (max 200, default 50).
        since_id: If > 0, return only messages with id greater than this value,
                  oldest first. Use the last returned message's id as your next
                  since_id to poll for new messages without re-fetching history.
    """
    conn = db.get_connection()
    try:
        channels = db.list_channels(conn)
        ch = next((c for c in channels if c["name"] == channel_name), None)
        if not ch:
            return []
        if since_id > 0:
            msgs = db.get_history(conn, ch["id"], limit=min(limit, 200), since_id=since_id)
        else:
            msgs = db.get_history(conn, ch["id"], limit=min(limit, 200))
            msgs = list(reversed(msgs))
        return [
            {
                "id": m["id"],
                "sender": m["sender"],
                "content": m["content"],
                "created_at": m["created_at"].isoformat() if m.get("created_at") else None,
            }
            for m in msgs
        ]
    finally:
        db.release_connection(conn)


@mcp.tool()
def grove_send_message(channel_name: str, content: str, sender: str = "claude-code") -> dict:
    """
    Send a message to a Grove channel. Creates the channel if it doesn't exist.

    Args:
        channel_name: Target channel name.
        content: Message body.
        sender: Display name for the sender (default: claude-code).
    """
    conn = db.get_connection()
    try:
        channels = db.list_channels(conn)
        ch = next((c for c in channels if c["name"] == channel_name), None)
        if not ch:
            ch = db.create_channel(conn, name=channel_name, channel_type="group")
        msg = db.send_message(conn, channel_id=ch["id"], sender=sender, content=content)
        return {"id": msg["id"], "channel": channel_name, "sent": True}
    finally:
        db.release_connection(conn)


@mcp.tool()
def grove_search(query: str, channel_name: str = "") -> list[dict]:
    """
    Search Grove messages by content.

    Args:
        query: Search term (case-insensitive substring match).
        channel_name: Optional channel to restrict search to.
    """
    conn = db.get_connection()
    try:
        channel_id = None
        if channel_name:
            channels = db.list_channels(conn)
            ch = next((c for c in channels if c["name"] == channel_name), None)
            channel_id = ch["id"] if ch else None
        msgs = db.search_messages(conn, query, channel_id=channel_id)
        return [
            {
                "sender": m["sender"],
                "content": m["content"],
                "created_at": m["created_at"].isoformat() if m.get("created_at") else None,
            }
            for m in msgs[:50]
        ]
    finally:
        db.release_connection(conn)


@mcp.tool()
def grove_get_identity() -> dict:
    """Get this Grove node's u2u address and public key."""
    from u2u.identity import Identity
    identity_path = Path.home() / ".willow" / "grove_identity.json"
    identity = Identity.load_or_generate(identity_path)
    name = os.getenv("GROVE_NAME", os.getenv("USER", "me"))
    port = int(os.getenv("GROVE_PORT", "8550"))
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            host = s.getsockname()[0]
    except OSError:
        host = "localhost"
    return {
        "address": f"{name}@{host}:{port}",
        "public_key": identity.public_key_hex,
    }


@mcp.tool()
async def grove_watch(channel_name: str, since_id: int, timeout: int = 30) -> list[dict]:
    """
    Block until new messages arrive in a channel, then return them.

    Polls every second for up to `timeout` seconds. Returns immediately when
    new messages are found. Returns an empty list on timeout (no new messages).
    Use the highest returned id as your next since_id call.

    Args:
        channel_name: Channel to watch (use grove_list_channels to find names).
        since_id: Return messages with id greater than this value.
        timeout: Max seconds to wait before returning empty (default 30, max 60).
    """
    deadline = asyncio.get_event_loop().time() + min(timeout, 60)
    channel_id = None
    while True:
        conn = db.get_connection()
        try:
            if channel_id is None:
                channels = db.list_channels(conn)
                ch = next((c for c in channels if c["name"] == channel_name), None)
                if not ch:
                    return []
                channel_id = ch["id"]
            msgs = db.get_history(conn, channel_id, limit=50, since_id=since_id)
        finally:
            db.release_connection(conn)  # release immediately — never hold during sleep

        if msgs:
            return [
                {
                    "id": m["id"],
                    "sender": m["sender"],
                    "content": m["content"],
                    "created_at": m["created_at"].isoformat() if m.get("created_at") else None,
                }
                for m in msgs
            ]

        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return []
        await asyncio.sleep(min(1.0, remaining))


@mcp.tool()
async def grove_watch_all(cursors: dict, timeout: int = 30) -> dict:
    """
    Watch multiple channels at once with a single DB connection per poll tick.

    Args:
        cursors: Dict mapping channel_name → since_id, e.g. {"general": 6, "architecture": 8}
        timeout: Max seconds to wait (default 30, max 60).

    Returns a dict mapping channel_name → list of new messages.
    Only channels with new messages appear in the result. Empty dict = timeout, nothing new.
    Use the highest id in each channel's result as your updated cursor.
    """
    deadline = asyncio.get_event_loop().time() + min(timeout, 60)
    channel_ids: dict[str, int] = {}

    while True:
        conn = db.get_connection()
        try:
            if not channel_ids:
                all_channels = db.list_channels(conn)
                for ch in all_channels:
                    if ch["name"] in cursors:
                        channel_ids[ch["name"]] = ch["id"]

            results: dict[str, list] = {}
            for name, cid in channel_ids.items():
                msgs = db.get_history(conn, cid, limit=50, since_id=cursors.get(name, 0))
                if msgs:
                    results[name] = [
                        {
                            "id": m["id"],
                            "sender": m["sender"],
                            "content": m["content"],
                            "created_at": m["created_at"].isoformat() if m.get("created_at") else None,
                        }
                        for m in msgs
                    ]
        finally:
            db.release_connection(conn)

        if results:
            return results

        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return {}
        await asyncio.sleep(min(1.0, remaining))


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
