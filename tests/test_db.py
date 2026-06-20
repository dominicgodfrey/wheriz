"""Tests for the SQLite schema and its invariants."""

import sqlite3

import pytest

from wwiw.db import connect, initialize


@pytest.fixture()
def conn():
    c = connect(":memory:")
    yield c
    c.close()


def _seed_search(conn: sqlite3.Connection) -> int:
    conn.execute("INSERT INTO zones (id, name) VALUES ('z1', 'Entrance')")
    conn.execute("INSERT INTO items (id, name, home_zone_id) VALUES ('i1', 'wallet', 'z1')")
    cur = conn.execute(
        "INSERT INTO searches (item_id, created_at) VALUES ('i1', '2026-06-19T20:00:00')"
    )
    conn.commit()
    return int(cur.lastrowid)


# --- schema -------------------------------------------------------------------


def test_all_expected_tables_exist(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = {r["name"] for r in rows}
    expected = {
        "zones", "zone_edges", "surfaces", "items", "priors", "failure_modes",
        "dwell_entries", "searches", "suggestions", "finds", "memory_log",
    }
    assert expected <= names


def test_initialize_is_idempotent(conn):
    initialize(conn)  # second run must not raise
    initialize(conn)


def test_foreign_keys_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO surfaces (id, zone_id, name) VALUES ('s1', 'missing_zone', 'counter')"
        )


def test_check_constraint_rejects_bad_zone_kind(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO zones (id, name, kind) VALUES ('z9', 'X', 'outdoors')")


def test_dwell_source_constraint(conn):
    conn.execute("INSERT INTO zones (id, name) VALUES ('z1', 'Kitchen')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO dwell_entries (zone_id, enter, exit, source) "
            "VALUES ('z1', '2026-06-19T18:00', '2026-06-19T18:40', 'sensor')"
        )


# --- append-only enforcement --------------------------------------------------


def test_finds_cannot_be_updated(conn):
    sid = _seed_search(conn)
    conn.execute(
        "INSERT INTO finds (search_id, zone_id, created_at) "
        "VALUES (?, 'z1', '2026-06-19T20:05:00')",
        (sid,),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE finds SET zone_id = 'z1' WHERE search_id = ?", (sid,))


def test_finds_cannot_be_deleted(conn):
    sid = _seed_search(conn)
    conn.execute(
        "INSERT INTO finds (search_id, zone_id, created_at) "
        "VALUES (?, 'z1', '2026-06-19T20:05:00')",
        (sid,),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM finds WHERE search_id = ?", (sid,))


def test_memory_log_is_append_only(conn):
    sid = _seed_search(conn)
    conn.execute(
        "INSERT INTO memory_log (search_id, created_at) VALUES (?, '2026-06-19T20:05:00')",
        (sid,),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM memory_log WHERE search_id = ?", (sid,))


def test_searches_cannot_be_deleted_but_status_can_advance(conn):
    sid = _seed_search(conn)
    # status lifecycle is allowed
    conn.execute("UPDATE searches SET status = 'found', followed_up = 1 WHERE id = ?", (sid,))
    conn.commit()
    assert conn.execute("SELECT status FROM searches WHERE id = ?", (sid,)).fetchone()[0] == "found"
    # deletion is not
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM searches WHERE id = ?", (sid,))
