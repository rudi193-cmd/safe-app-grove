# bridge/app.py — Grove ↔ Matrix bridge coordinator
"""
Flow summary
============

Matrix user → Grove user
  1. Matrix user invites @grove_<hex_addr>:<hs> to a DM room
  2. ASServer receives m.room.member/invite
  3. Bridge sends u2u KNOCK to Grove addr
  4. Grove user sees KNOCK in their TUI, approves
  5. Grove user sends a NOTE back to bridge (implicit approval signal)
  6. Bridge marks mapping active, delivers NOTE as Matrix message
  7. Subsequent Matrix messages → u2u NOTEs, and vice versa

Grove user → Matrix user
  1. Grove user KNOCKs the bridge at bridge_addr
  2. Bridge receives KNOCK, creates Matrix DM room, invites the target Matrix user
     (target must be pre-configured or resolved from a directory — out of scope for now)
  Currently: Grove-initiated flow requires the Matrix user to initiate first.
"""

import asyncio
import logging
import socket
from pathlib import Path

from aiohttp import web

from u2u import dispatcher
from u2u.consent import ConsentGate
from u2u.contacts import ContactStore
from u2u.identity import Identity
from u2u.listener import U2UListener
from u2u.packets import PacketType
from u2u.sender import send_packet

from .matrix import ASServer, MatrixClient
from .store import BridgeStore

log = logging.getLogger("bridge.app")


def _addr_to_localpart(addr: str) -> str:
    """grove addr → hex string safe for Matrix localpart."""
    return addr.encode().hex()


def _localpart_to_addr(localpart: str) -> str:
    """Reverse of _addr_to_localpart. Raises ValueError on bad input."""
    return bytes.fromhex(localpart).decode()


def _puppet_id(grove_addr: str, hs_name: str) -> str:
    return f"@grove_{_addr_to_localpart(grove_addr)}:{hs_name}"


def _resolve_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "localhost"


class GroveMatrixBridge:
    def __init__(
        self,
        homeserver: str,
        hs_name: str,
        as_token: str,
        hs_token: str,
        grove_port: int,
        as_port: int,
        identity_path: Path,
        store_path: Path,
    ):
        self._hs_name    = hs_name
        self._grove_port = grove_port
        self._as_port    = as_port

        self.identity = Identity.load_or_generate(identity_path)
        self.store    = BridgeStore(store_path)

        local_ip = _resolve_local_ip()
        self._bridge_addr = f"grove_bridge@{local_ip}:{grove_port}"

        contacts_path = identity_path.parent / "grove_bridge_contacts.json"
        self.contacts = ContactStore(contacts_path)

        self._bot_id = f"@grove_bridge:{hs_name}"
        self.matrix  = MatrixClient(homeserver, as_token, self._bot_id)
        self._as_srv = ASServer(hs_token, self._on_matrix_event)

        log.info("bridge addr: %s", self._bridge_addr)

    async def run(self) -> None:
        await self.matrix.start()
        try:
            await asyncio.gather(
                self._run_grove_listener(),
                self._run_as_server(),
            )
        finally:
            await self.matrix.close()

    # ── Grove listener ────────────────────────────────────────────────────────

    async def _run_grove_listener(self) -> None:
        gate = ConsentGate(self.contacts)

        def _on_note(packet: dict) -> None:
            h         = packet["header"]
            pl        = packet.get("payload", {})
            from_addr = h.get("from", "")
            body      = pl.get("body", "")
            if h.get("_denied"):
                log.debug("denied NOTE from %s", from_addr)
                return
            asyncio.get_event_loop().create_task(
                self._grove_note(from_addr, body)
            )

        def _on_knock(packet: dict) -> None:
            h         = packet["header"]
            pl        = packet.get("payload", {})
            from_addr = h.get("from", "")
            pubkey    = pl.get("public_key", "")
            asyncio.get_event_loop().create_task(
                self._grove_knock(from_addr, pubkey)
            )

        dispatcher.register(PacketType.NOTE,  _on_note)
        dispatcher.register(PacketType.KNOCK, _on_knock)

        listener = U2UListener(
            host="0.0.0.0", port=self._grove_port,
            identity=self.identity, consent=gate,
        )
        async with listener.serve():
            await asyncio.Event().wait()

    async def _grove_note(self, from_addr: str, body: str) -> None:
        """Grove NOTE → Matrix message. First NOTE from a pending addr = approval."""
        mapping = self.store.get_by_grove_addr(from_addr)
        if not mapping:
            log.warning("NOTE from unmapped Grove addr %s — dropped", from_addr)
            return

        if mapping["state"] == "pending_knock":
            # First NOTE = implicit KNOCK approval. Admit them to contacts and activate.
            pending = self.store.get_pending_knock(from_addr)
            if pending and pending["public_key"]:
                self.contacts.add(from_addr, pending["public_key"])
                self.store.clear_pending_knock(from_addr)
            self.store.activate(from_addr)
            await self.matrix.send_notice(
                mapping["matrix_room"],
                f"Grove user {from_addr} accepted the connection.",
            )

        puppet = _puppet_id(from_addr, self._hs_name)
        await self.matrix.ensure_virtual_user(puppet)
        await self.matrix.send_message(mapping["matrix_room"], body, as_user=puppet)
        log.info("Grove→Matrix  %s → %s", from_addr, mapping["matrix_room"])

    async def _grove_knock(self, from_addr: str, pubkey: str) -> None:
        """
        Grove KNOCK → bridge queues it. Matrix user must have already invited
        the puppet (creating a mapping) for this to activate. Otherwise just
        store the pubkey for when the invite arrives.
        """
        mapping = self.store.get_by_grove_addr(from_addr)
        if mapping and mapping["state"] == "active":
            # Re-knock from known contact — update key silently
            if pubkey:
                self.contacts.add(from_addr, pubkey)
            return

        self.store.set_pending_knock(from_addr, pubkey)
        log.info("queued KNOCK from %s", from_addr)

    # ── Matrix AS server ──────────────────────────────────────────────────────

    async def _run_as_server(self) -> None:
        app   = self._as_srv.build_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self._as_port)
        await site.start()
        log.info("AS server on port %d", self._as_port)
        await asyncio.Event().wait()

    async def _on_matrix_event(self, event: dict) -> None:
        etype = event.get("type")
        if etype == "m.room.member":
            await self._mx_member(event)
        elif etype == "m.room.message":
            await self._mx_message(event)

    async def _mx_member(self, event: dict) -> None:
        """Matrix invite to a grove_* puppet → send KNOCK to that Grove addr."""
        if event.get("content", {}).get("membership") != "invite":
            return

        state_key = event.get("state_key", "")   # the invited user
        room_id   = event.get("room_id", "")
        inviter   = event.get("sender", "")

        if not state_key.startswith("@grove_"):
            return

        localpart = state_key.split(":")[0].lstrip("@")[len("grove_"):]

        # Bridge bot itself invited → just join
        if localpart == "bridge":
            await self.matrix.join_room(room_id, as_user=self._bot_id)
            return

        try:
            grove_addr = _localpart_to_addr(localpart)
        except (ValueError, UnicodeDecodeError):
            log.warning("bad grove addr in puppet %s", state_key)
            return

        # Persist mapping (pending until Grove user sends their first NOTE)
        self.store.upsert(grove_addr, room_id, inviter, state="pending_knock")

        # Accept the invite on behalf of the puppet
        puppet = state_key
        await self.matrix.ensure_virtual_user(puppet)
        await self.matrix.join_room(room_id, as_user=puppet)

        # Send u2u KNOCK to the Grove user
        ok = await send_packet(
            PacketType.KNOCK,
            self._bridge_addr,
            grove_addr,
            {"public_key": self.identity.public_key_hex},
            self.identity,
        )

        notice = (
            f"Knocked Grove user at {grove_addr}. Waiting for them to approve."
            if ok else
            f"Could not reach Grove user at {grove_addr} — are they online?"
        )
        await self.matrix.send_notice(room_id, notice)
        log.info("Matrix→Grove KNOCK: %s → %s (ok=%s)", inviter, grove_addr, ok)

    async def _mx_message(self, event: dict) -> None:
        """Matrix message → u2u NOTE to Grove user."""
        sender  = event.get("sender", "")
        room_id = event.get("room_id", "")
        body    = event.get("content", {}).get("body", "")

        # Ignore messages from our own puppets to avoid loops
        if sender.startswith("@grove_"):
            return

        mapping = self.store.get_by_matrix_room(room_id)
        if not mapping:
            return

        grove_addr = mapping["grove_addr"]

        if mapping["state"] == "pending_knock":
            await self.matrix.send_notice(
                room_id,
                "Waiting for Grove user to approve the connection. Message not sent.",
            )
            return

        ok = await send_packet(
            PacketType.NOTE,
            self._bridge_addr,
            grove_addr,
            {"subject": "", "body": body},
            self.identity,
        )
        if not ok:
            await self.matrix.send_notice(room_id, "Grove user appears offline — message not delivered.")
        log.info("Matrix→Grove NOTE: %s → %s (ok=%s)", sender, grove_addr, ok)
