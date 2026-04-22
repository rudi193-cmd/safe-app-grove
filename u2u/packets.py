# u2u/packets.py
# b17: U2UP1
"""U2U packet format — U2U-WIRE-1. Signed JSON, newline-delimited."""

import json
import time
from enum import Enum
from typing import Any

from u2u.identity import Identity


class PacketType(str, Enum):
    KNOCK = "KNOCK"
    NOTE  = "NOTE"
    ASK   = "ASK"
    REPLY = "REPLY"
    ALERT = "ALERT"
    SHARE = "SHARE"


class PacketError(Exception):
    pass


class Packet:
    @staticmethod
    def build(
        ptype: PacketType,
        from_addr: str,
        to_addr: str,
        payload: dict[str, Any],
        identity: Identity,
        ttl: int = 86400,
        thread_id: str | None = None,
    ) -> dict:
        now = int(time.time())
        payload_json = json.dumps(payload, sort_keys=True)
        header = {
            "version": "u2u-1",
            "type": ptype.value,
            "from": from_addr,
            "to": to_addr,
            "sent_at": now,
            "expires_at": now + ttl,
            "thread_id": thread_id,
        }
        signing_input = (json.dumps(header, sort_keys=True) + payload_json).encode()
        header["sig"] = identity.sign(signing_input)
        return {"header": header, "payload": payload}

    @staticmethod
    def validate(packet: dict, sender_public_key_hex: str) -> bool:
        try:
            header = dict(packet["header"])
            sig = header.pop("sig")
            now = int(time.time())
            if header.get("expires_at", 0) < now:
                return False
            payload_json = json.dumps(packet["payload"], sort_keys=True)
            signing_input = (json.dumps(header, sort_keys=True) + payload_json).encode()
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            from cryptography.exceptions import InvalidSignature
            pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(sender_public_key_hex))
            try:
                pub.verify(bytes.fromhex(sig), signing_input)
                return True
            except InvalidSignature:
                return False
        except Exception:
            return False

    @staticmethod
    def serialize(packet: dict) -> bytes:
        return (json.dumps(packet, separators=(",", ":")) + "\n").encode()

    @staticmethod
    def deserialize(data: bytes) -> dict:
        try:
            return json.loads(data.strip())
        except json.JSONDecodeError as e:
            raise PacketError(f"invalid packet JSON: {e}") from e
