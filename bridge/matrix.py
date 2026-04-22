# bridge/matrix.py — Matrix CS API client + Application Service HTTP server
import logging
import time
from aiohttp import web, ClientSession, ClientTimeout

log = logging.getLogger("bridge.matrix")

_TIMEOUT = ClientTimeout(total=15)


class MatrixClient:
    """Minimal Matrix Client-Server API wrapper for a bridge bot."""

    def __init__(self, homeserver: str, as_token: str, bot_user_id: str):
        self._hs        = homeserver.rstrip("/")
        self._token     = as_token
        self._bot       = bot_user_id
        self._session: ClientSession | None = None

    async def start(self) -> None:
        self._session = ClientSession(
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=_TIMEOUT,
        )

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    def _cs(self, path: str) -> str:
        return f"{self._hs}/_matrix/client/v3{path}"

    async def ensure_virtual_user(self, user_id: str) -> None:
        """Register virtual user if not already registered. Idempotent."""
        localpart = user_id.split(":")[0].lstrip("@")
        async with self._session.post(
            f"{self._hs}/_matrix/client/v3/register",
            params={"user_id": self._bot},
            json={"type": "m.login.application_service", "username": localpart},
        ) as r:
            if r.status not in (200, 400):
                log.warning("register %s → %s", user_id, r.status)

    async def join_room(self, room_id: str, as_user: str) -> bool:
        async with self._session.post(
            self._cs(f"/join/{room_id}"),
            params={"user_id": as_user},
            json={},
        ) as r:
            ok = r.status == 200
            if not ok:
                log.warning("join_room %s as %s → %s", room_id, as_user, r.status)
            return ok

    async def send_message(self, room_id: str, body: str, as_user: str) -> bool:
        txn_id = str(int(time.monotonic() * 1_000_000))
        async with self._session.put(
            self._cs(f"/rooms/{room_id}/send/m.room.message/{txn_id}"),
            params={"user_id": as_user},
            json={"msgtype": "m.text", "body": body},
        ) as r:
            ok = r.status == 200
            if not ok:
                log.warning("send_message %s → %s", room_id, r.status)
            return ok

    async def send_notice(self, room_id: str, body: str) -> bool:
        """Send a bridge status notice as the bot itself."""
        return await self.send_message(room_id, f"[bridge] {body}", as_user=self._bot)


class ASServer:
    """
    Receives transaction events POSTed by the homeserver.
    Passes each event to on_event(event: dict) coroutine.
    """

    def __init__(self, hs_token: str, on_event):
        self._hs_token  = hs_token
        self._on_event  = on_event
        self._seen: set[str] = set()

    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_put("/transactions/{txn_id}", self._handle_txn)
        return app

    async def _handle_txn(self, request: web.Request) -> web.Response:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token != self._hs_token:
            return web.Response(status=403)

        txn_id = request.match_info["txn_id"]
        if txn_id in self._seen:
            return web.json_response({})
        self._seen.add(txn_id)

        body = await request.json()
        for event in body.get("events", []):
            try:
                await self._on_event(event)
            except Exception as e:
                log.error("event handler error: %s", e)

        return web.json_response({})
