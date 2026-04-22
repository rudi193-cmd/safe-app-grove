# grove/mcp_local.py — Grove stdio MCP for Claude Code.
# b17: GRMLC  ΔΣ=42
"""
Local stdio transport — no OAuth, no tunnel, no port.
Run via .mcp.json:
  "grove": { "command": "python3", "args": ["-m", "grove.mcp_local"] }

Same tools as mcp_server.py. Auth is implicit (local process, trusted user).
"""
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
def grove_get_history(channel_name: str, limit: int = 50) -> list[dict]:
    """
    Get message history from a Grove channel.

    Args:
        channel_name: Exact channel name (use grove_list_channels to find names).
        limit: Number of messages to return (max 200, default 50).
    """
    conn = db.get_connection()
    try:
        channels = db.list_channels(conn)
        ch = next((c for c in channels if c["name"] == channel_name), None)
        if not ch:
            return []
        msgs = db.get_history(conn, ch["id"], limit=min(limit, 200))
        return [
            {
                "sender": m["sender"],
                "content": m["content"],
                "created_at": m["created_at"].isoformat() if m.get("created_at") else None,
            }
            for m in reversed(msgs)
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


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
