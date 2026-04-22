# grove/mcp_auth.py — Single-user OAuth 2.0 provider for Grove MCP.
# b17: GRMCA
"""
In-memory auth: dynamic client registration (claude.ai uses this),
PKCE auth code flow, long-lived access token stored in ~/.willow/grove_mcp_token.
"""
import secrets
import time
from pathlib import Path

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


class GroveOAuthProvider:
    def __init__(self, token_path: Path, base_url: str):
        self._token_path = token_path
        self._base_url = base_url.rstrip("/")
        self._access_token = self._load_or_generate()
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._pending: dict[str, tuple[OAuthClientInformationFull, AuthorizationParams]] = {}

    def _load_or_generate(self) -> str:
        if self._token_path.exists():
            return self._token_path.read_text().strip()
        token = "grove_" + secrets.token_urlsafe(32)
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(token)
        return token

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._clients[client_info.client_id] = client_info

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        key = secrets.token_urlsafe(16)
        self._pending[key] = (client, params)
        return f"{self._base_url}/grove-approve?pending={key}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self._codes.get(authorization_code)
        if code and code.expires_at > time.time():
            return code
        return None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self._codes.pop(authorization_code.code, None)
        return OAuthToken(access_token=self._access_token, token_type="Bearer")

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        raise TokenError("invalid_grant")

    async def load_access_token(self, token: str) -> AccessToken | None:
        if token == self._access_token:
            return AccessToken(token=token, client_id="grove-mcp", scopes=["grove"])
        return None

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        pass

    # ── Helpers for the /grove-approve route ─────────────────────────────────

    def pop_pending(self, key: str) -> tuple[OAuthClientInformationFull, AuthorizationParams] | None:
        return self._pending.pop(key, None)

    def issue_code(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or ["grove"],
            expires_at=time.time() + 300,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
        )
        return code
