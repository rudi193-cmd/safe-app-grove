# u2u/listener.py
# b17: U2UL1
"""U2U TCP listener — asyncio server, port 8550 by default."""

import asyncio
import logging
from contextlib import asynccontextmanager

from u2u import dispatcher
from u2u.consent import ConsentGate, ConsentResult
from u2u.identity import Identity
from u2u.packets import Packet, PacketError, PacketType

log = logging.getLogger("u2u.listener")

DEFAULT_PORT = 8550
_MAX_PACKET_BYTES = 16_384


class U2UListener:
    def __init__(self, host: str, port: int, identity: Identity, consent: ConsentGate):
        self.host    = host
        self.port    = port
        self._ident  = identity
        self._consent = consent
        self._server  = None

    @asynccontextmanager
    async def serve(self):
        self._server = await asyncio.start_server(
            self._handle, self.host, self.port,
            limit=_MAX_PACKET_BYTES,
        )
        log.info("U2U listening on %s:%s", self.host, self.port)
        async with self._server:
            yield self

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=10.0)
            if len(raw) > _MAX_PACKET_BYTES:
                log.warning("oversized packet from %s — dropped", peer)
                return
            packet = Packet.deserialize(raw)
            await self._process(packet, peer)
        except (PacketError, asyncio.TimeoutError) as e:
            log.warning("bad packet from %s: %s", peer, e)
        finally:
            writer.close()

    async def _process(self, packet: dict, peer):
        sender_addr = packet.get("header", {}).get("from", "")
        ptype_str   = packet.get("header", {}).get("type", "")

        try:
            ptype = PacketType(ptype_str)
        except ValueError:
            log.warning("unknown packet type %r from %s", ptype_str, peer)
            return

        result = self._consent.check(sender_addr, ptype)
        if result == ConsentResult.DENY:
            log.debug("denied %s from %s", ptype_str, sender_addr)
            if ptype == PacketType.NOTE:
                dispatcher.dispatch({
                    "header": {**packet["header"], "_denied": True},
                    "payload": {},
                })
            return
        if result == ConsentResult.PENDING:
            log.info("KNOCK pending approval from %s", sender_addr)
            dispatcher.dispatch({
                "header": {**packet["header"], "_pending": True},
                "payload": packet.get("payload", {}),
            })
            return

        contact = self._consent.get_contact(sender_addr)
        if not contact or not Packet.validate(packet, contact.public_key_hex):
            log.warning("invalid sig from %s — dropped", sender_addr)
            return

        dispatcher.dispatch(packet)
        log.info("dispatched %s from %s", ptype_str, sender_addr)
