# bridge/store.py — SQLite mapping: grove addr ↔ Matrix room ↔ Matrix user
import sqlite3
from pathlib import Path
from typing import Optional


class BridgeStore:
    def __init__(self, path: Path):
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS mappings (
                grove_addr   TEXT PRIMARY KEY,
                matrix_room  TEXT NOT NULL,
                matrix_user  TEXT NOT NULL,
                state        TEXT NOT NULL DEFAULT 'pending_knock'
            );
            CREATE TABLE IF NOT EXISTS pending_knocks (
                grove_addr  TEXT PRIMARY KEY,
                public_key  TEXT NOT NULL,
                created_at  REAL DEFAULT (unixepoch('now'))
            );
        """)

    def get_by_grove_addr(self, addr: str) -> Optional[sqlite3.Row]:
        return self._db.execute(
            "SELECT * FROM mappings WHERE grove_addr = ?", (addr,)
        ).fetchone()

    def get_by_matrix_room(self, room_id: str) -> Optional[sqlite3.Row]:
        return self._db.execute(
            "SELECT * FROM mappings WHERE matrix_room = ?", (room_id,)
        ).fetchone()

    def upsert(self, grove_addr: str, matrix_room: str, matrix_user: str,
               state: str = "pending_knock") -> None:
        self._db.execute("""
            INSERT INTO mappings (grove_addr, matrix_room, matrix_user, state)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (grove_addr) DO UPDATE SET
                matrix_room = excluded.matrix_room,
                matrix_user = excluded.matrix_user,
                state       = excluded.state
        """, (grove_addr, matrix_room, matrix_user, state))
        self._db.commit()

    def activate(self, grove_addr: str) -> None:
        self._db.execute(
            "UPDATE mappings SET state = 'active' WHERE grove_addr = ?", (grove_addr,)
        )
        self._db.commit()

    def set_pending_knock(self, grove_addr: str, public_key: str) -> None:
        self._db.execute("""
            INSERT OR REPLACE INTO pending_knocks (grove_addr, public_key)
            VALUES (?, ?)
        """, (grove_addr, public_key))
        self._db.commit()

    def get_pending_knock(self, grove_addr: str) -> Optional[sqlite3.Row]:
        return self._db.execute(
            "SELECT * FROM pending_knocks WHERE grove_addr = ?", (grove_addr,)
        ).fetchone()

    def clear_pending_knock(self, grove_addr: str) -> None:
        self._db.execute(
            "DELETE FROM pending_knocks WHERE grove_addr = ?", (grove_addr,)
        )
        self._db.commit()
