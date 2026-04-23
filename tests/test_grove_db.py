"""tests/test_grove_db.py — grove_db integration tests against a real Postgres instance."""
import os
import sys
import pytest
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import grove_db as db


@pytest.fixture(scope="session")
def conn():
    c = psycopg2.connect(
        dbname=os.environ.get("PGDATABASE", "postgres"),
        user=os.environ.get("PGUSER", "postgres"),
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
        password=os.environ.get("PGPASSWORD", "postgres"),
    )
    db.init_schema(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def clean(conn):
    yield
    conn.rollback()
    cur = conn.cursor()
    cur.execute("DELETE FROM grove.messages")
    cur.execute("DELETE FROM grove.channels")
    conn.commit()


def test_init_schema_idempotent(conn):
    db.init_schema(conn)  # second call must not raise


def test_create_and_list_channel(conn):
    ch = db.create_channel(conn, name="general", channel_type="group", description="test")
    assert ch["name"] == "general"
    channels = db.list_channels(conn)
    assert any(c["name"] == "general" for c in channels)


def test_send_and_get_history(conn):
    ch = db.create_channel(conn, name="chat", channel_type="group")
    db.send_message(conn, channel_id=ch["id"], sender="sean", content="hello")
    db.send_message(conn, channel_id=ch["id"], sender="sean", content="world")
    history = db.get_history(conn, ch["id"])
    assert len(history) == 2


def test_search_messages(conn):
    ch = db.create_channel(conn, name="search-test", channel_type="group")
    db.send_message(conn, channel_id=ch["id"], sender="sean", content="find me please")
    db.send_message(conn, channel_id=ch["id"], sender="sean", content="nothing here")
    results = db.search_messages(conn, "find me")
    assert len(results) == 1
    assert "find me" in results[0]["content"]


def test_delete_message(conn):
    ch = db.create_channel(conn, name="del-test", channel_type="group")
    msg = db.send_message(conn, channel_id=ch["id"], sender="sean", content="delete me")
    db.delete_message(conn, msg["id"])
    history = db.get_history(conn, ch["id"])
    assert all(m["id"] != msg["id"] for m in history)


def test_mark_indexed(conn):
    ch = db.create_channel(conn, name="idx-test", channel_type="group")
    msg = db.send_message(conn, channel_id=ch["id"], sender="sean", content="index me")
    unindexed = db.get_unindexed(conn)
    ids = [m["id"] for m in unindexed]
    assert msg["id"] in ids
    db.mark_indexed(conn, [msg["id"]])
    unindexed_after = db.get_unindexed(conn)
    assert msg["id"] not in [m["id"] for m in unindexed_after]
