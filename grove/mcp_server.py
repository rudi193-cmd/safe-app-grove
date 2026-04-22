# grove/mcp_server.py — Grove HTTP MCP server for claude.ai.
# b17: GR8MC
"""
Transport: StreamableHTTP (MCP 2025-03-26)
Auth:      OAuth 2.0 + PKCE, single-user, long-lived token

Start:
  GROVE_MCP_URL=https://your-id.lhr.life python -m grove.mcp_server

Connect to claude.ai:
  1. Run: ssh -R 80:localhost:8551 localhost.run
     (prints your HTTPS URL, e.g. https://abc123.lhr.life)
  2. claude.ai → Settings → Integrations → Add Integration → paste that URL
  3. Authorize in the browser prompt that appears
"""
import os
import socket
from pathlib import Path

from mcp.server.auth.provider import construct_redirect_uri
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse

import grove_db as db
from grove.mcp_auth import GroveOAuthProvider

# ── Config ────────────────────────────────────────────────────────────────────
_PORT     = int(os.getenv("GROVE_MCP_PORT", "8551"))
_HOST     = os.getenv("GROVE_MCP_HOST", "0.0.0.0")
_BASE_URL = os.getenv("GROVE_MCP_URL", f"http://localhost:{_PORT}")
_TOKEN_PATH = Path.home() / ".willow" / "grove_mcp_token"

_provider = GroveOAuthProvider(_TOKEN_PATH, _BASE_URL)

mcp = FastMCP(
    "Grove",
    instructions=(
        "Grove sovereign workspace messaging. "
        "Send and read messages, search conversations, list channels."
    ),
    auth_server_provider=_provider,
    auth=AuthSettings(
        issuer_url=_BASE_URL,           # type: ignore[arg-type]
        resource_server_url=_BASE_URL,  # type: ignore[arg-type]
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["grove"],
            default_scopes=["grove"],
        ),
    ),
    host=_HOST,
    port=_PORT,
    stateless_http=True,
    streamable_http_path="/",
)


# ── OAuth approval page ───────────────────────────────────────────────────────

@mcp.custom_route("/grove-approve", methods=["GET"])
async def grove_approve(request: Request) -> HTMLResponse:
    """
    The user lands here after claude.ai redirects for authorization.
    We show a simple "Authorize" page. The code is pre-generated and
    embedded in the button link — clicking it completes the OAuth flow.
    """
    key = request.query_params.get("pending", "")
    entry = _provider.pop_pending(key)
    if not entry:
        return HTMLResponse(
            "<h1 style='font-family:monospace'>Link expired or already used.</h1>",
            status_code=400,
        )
    client, params = entry
    code = _provider.issue_code(client, params)
    redirect_url = construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Grove — Authorize</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Courier New', monospace;
      background: #0f1117;
      color: #e2e8f0;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
    }}
    .card {{
      background: #1a202c;
      border: 1px solid #2d3748;
      border-radius: 8px;
      padding: 2.5rem 3rem;
      text-align: center;
      max-width: 420px;
      width: 90%;
    }}
    h1 {{ color: #68d391; font-size: 1.5rem; margin-bottom: 0.4rem; }}
    .subtitle {{ color: #718096; font-size: 0.85rem; margin-bottom: 1.5rem; }}
    .client {{ color: #90cdf4; font-weight: bold; }}
    .scopes {{
      background: #171e2e;
      border: 1px solid #2d3748;
      border-radius: 4px;
      padding: 0.75rem 1rem;
      margin-bottom: 1.5rem;
      font-size: 0.85rem;
      color: #a0aec0;
      text-align: left;
    }}
    .btn {{
      display: inline-block;
      background: #276749;
      color: #fff;
      text-decoration: none;
      padding: 0.75rem 2.5rem;
      border-radius: 4px;
      font-size: 1rem;
      font-family: inherit;
      transition: background 0.15s;
    }}
    .btn:hover {{ background: #2f855a; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Grove MCP</h1>
    <p class="subtitle">Authorization request</p>
    <p style="margin-bottom:1rem; color:#a0aec0;">
      <span class="client">{client.client_id}</span> is requesting access to your Grove workspace.
    </p>
    <div class="scopes">
      <strong>Permissions:</strong><br>
      • Read channels and message history<br>
      • Send messages<br>
      • Search conversations
    </div>
    <a class="btn" href="{redirect_url}">Authorize Grove</a>
  </div>
</body>
</html>""")


# ── Schema init on startup ────────────────────────────────────────────────────
# _get_pool() bootstraps schema on first use, but call explicitly here so any
# schema error surfaces at startup rather than on first tool call.
try:
    _startup_conn = db.get_connection()
    db.release_connection(_startup_conn)
except Exception as _e:
    import sys as _sys
    print(f"[grove-mcp] WARNING: could not verify grove schema: {_e}", file=_sys.stderr)


# ── Grove tools ───────────────────────────────────────────────────────────────

@mcp.tool()
def grove_list_channels() -> list[dict]:
    """List all active Grove channels (name, type, description)."""
    conn = db.get_connection()
    try:
        rows = db.list_channels(conn)
        return [
            {"id": r["id"], "name": r["name"], "type": r["channel_type"], "description": r.get("description")}
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
def grove_send_message(channel_name: str, content: str, sender: str = "claude-ai") -> dict:
    """
    Send a message to a Grove channel. Creates the channel if it doesn't exist.

    Args:
        channel_name: Target channel name.
        content: Message body.
        sender: Display name for the sender (default: claude-ai).
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
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
