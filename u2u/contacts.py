# u2u/contacts.py
# b17: U2UC1
"""U2U contact store — ~/.willow/grove_contacts.json"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, UTC
from pathlib import Path
from typing import Optional


@dataclass
class Contact:
    addr: str
    public_key_hex: str
    name: str = ""
    blocked: bool = False
    consent_note: bool = True
    consent_ask: bool = True
    consent_alert: bool = False
    consent_share: bool = True
    added: str = ""


class ContactStore:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._contacts: dict[str, Contact] = {}
        if self._path.exists():
            self._load()

    def _load(self):
        try:
            raw = json.loads(self._path.read_text())
            for addr, data in raw.items():
                self._contacts[addr] = Contact(**data)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"Cannot load contacts from {self._path}: {e}") from e

    def save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(
            {addr: asdict(c) for addr, c in self._contacts.items()},
            indent=2,
        ))

    def add(self, addr: str, public_key_hex: str, name: str = "") -> Contact:
        c = Contact(
            addr=addr, public_key_hex=public_key_hex, name=name,
            added=datetime.now(UTC).isoformat(),
        )
        self._contacts[addr] = c
        self.save()
        return c

    def get(self, addr: str) -> Optional[Contact]:
        return self._contacts.get(addr)

    def block(self, addr: str) -> bool:
        if addr in self._contacts:
            self._contacts[addr].blocked = True
            self.save()
            return True
        return False

    def all(self) -> list[Contact]:
        return list(self._contacts.values())
