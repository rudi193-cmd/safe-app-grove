# u2u/consent.py
# b17: U2UG1
"""U2U consent gate — allow/deny/pending logic."""

from enum import Enum
from u2u.contacts import ContactStore
from u2u.packets import PacketType


class ConsentResult(str, Enum):
    ALLOW   = "allow"
    DENY    = "deny"
    PENDING = "pending"


_TYPE_TO_FIELD = {
    PacketType.NOTE:  "consent_note",
    PacketType.ASK:   "consent_ask",
    PacketType.ALERT: "consent_alert",
    PacketType.SHARE: "consent_share",
}


class ConsentGate:
    def __init__(self, store: ContactStore):
        self._store = store

    def get_contact(self, addr: str):
        return self._store.get(addr)

    def check(self, sender_addr: str, ptype: PacketType) -> ConsentResult:
        contact = self._store.get(sender_addr)

        if contact is None:
            return ConsentResult.PENDING if ptype == PacketType.KNOCK else ConsentResult.DENY

        if contact.blocked:
            return ConsentResult.DENY

        if ptype in (PacketType.KNOCK, PacketType.REPLY):
            return ConsentResult.ALLOW

        field = _TYPE_TO_FIELD.get(ptype)
        if field and not getattr(contact, field, False):
            return ConsentResult.DENY

        return ConsentResult.ALLOW
