# u2u/sender.py
# b17: U2US1
"""U2U outbound packet sender — sign and deliver over TCP."""

import asyncio
import logging
from u2u.identity import Identity
from u2u.packets import Packet, PacketType

log = logging.getLogger("u2u.sender")

_CONNECT_TIMEOUT = 10.0
_WRITE_TIMEOUT   = 5.0


def _parse_endpoint(addr: str) -> tuple[str, int]:
    """Parse 'user@host:port' → ('host', port)."""
    _, endpoint = addr.rsplit("@", 1)
    host, port = endpoint.rsplit(":", 1)
    return host, int(port)


async def send_packet(
    ptype: PacketType,
    from_addr: str,
    to_addr: str,
    payload: dict,
    identity: Identity,
    ttl: int = 86400,
    thread_id: str | None = None,
) -> bool:
    packet = Packet.build(ptype, from_addr, to_addr, payload, identity, ttl, thread_id)
    wire = Packet.serialize(packet)
    try:
        host, port = _parse_endpoint(to_addr)
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=_CONNECT_TIMEOUT,
        )
        writer.write(wire)
        await asyncio.wait_for(writer.drain(), timeout=_WRITE_TIMEOUT)
        writer.close()
        await writer.wait_closed()
        log.info("sent %s → %s", ptype.value, to_addr)
        return True
    except (OSError, asyncio.TimeoutError, ValueError) as e:
        log.warning("send failed to %s: %s", to_addr, e)
        return False


def send(ptype: PacketType, from_addr: str, to_addr: str,
         payload: dict, identity: Identity, **kwargs) -> bool:
    """Sync wrapper. Raises RuntimeError if called inside a running event loop — use send_packet() directly there."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        raise RuntimeError(
            "u2u.send() called inside a running event loop. "
            "Use 'await send_packet()' instead."
        )
    return asyncio.run(send_packet(ptype, from_addr, to_addr, payload, identity, **kwargs))
