"""Tests for the find-loop DB access layer: searches, suggestions, finds, the silent
memory log, the occupancy timeline, and the learned-distribution read/write round-trips.

In-memory database; reads of the timeline come back as engine ``DwellEntry`` dataclasses,
the source-agnostic shape the engine ranks on.
"""

from datetime import datetime, timedelta

import pytest

from wwiw import db
from wwiw.engine.types import DwellEntry, Item, Surface


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def home(conn):
    """A minimal set-up home: three connected zones, one item, one surface."""
    kitchen = db.create_zone(conn, "Kitchen")
    hallway = db.create_zone(conn, "Hallway", kind="transit")
    couch = db.create_zone(conn, "Living Room")
    db.add_edge(conn, kitchen, hallway)
    db.add_edge(conn, hallway, couch)
    db.create_surface(conn, kitchen, "Counter", source="photo")
    keys = db.create_item(conn, "Keys", home_zone_id=kitchen)
    return {"kitchen": kitchen, "hallway": hallway, "living_room": couch, "keys": keys}


NOW = datetime(2026, 6, 20, 18, 0, 0)


# --- item / surface lookups ---------------------------------------------------


def test_get_item_and_by_name(conn, home):
    assert db.get_item(conn, "keys") == Item(id="keys", name="Keys", home_zone_id="kitchen")
    assert db.get_item_by_name(conn, "KEYS").id == "keys"
    assert db.get_item(conn, "ghost") is None
    assert db.get_item_by_name(conn, "ghost") is None


def test_first_surface_id_is_a_hint_or_none(conn, home):
    assert db.get_surface(conn, db.first_surface_id(conn, "kitchen")) == Surface(
        id="kitchen-counter", zone_id="kitchen", name="Counter", source="photo"
    )
    assert db.first_surface_id(conn, "living-room") is None  # no surfaces here


# --- learned distributions ----------------------------------------------------


def test_priors_round_trip(conn, home):
    db.write_priors(conn, "keys", {"kitchen": 0.7, "living-room": 0.3})
    assert db.read_priors(conn, "keys") == {"kitchen": 0.7, "living-room": 0.3}
    db.write_priors(conn, "keys", {"kitchen": 0.5})  # upsert, leaves others
    assert db.read_priors(conn, "keys") == {"kitchen": 0.5, "living-room": 0.3}


def test_failure_modes_round_trip_and_count_bump(conn, home):
    db.write_failure_modes(conn, "keys", {"living-room": 1.0}, bumped_zone_id="living-room")
    db.write_failure_modes(conn, "keys", {"living-room": 1.9}, bumped_zone_id="living-room")
    assert db.read_failure_modes(conn, "keys") == {"living-room": 1.9}
    count = conn.execute(
        "SELECT count FROM failure_modes WHERE item_id='keys' AND zone_id='living-room'"
    ).fetchone()[0]
    assert count == 2  # bumped once per away-from-home find


def test_failure_mode_write_without_bump_leaves_count(conn, home):
    db.write_failure_modes(conn, "keys", {"living-room": 1.0}, bumped_zone_id="living-room")
    db.write_failure_modes(conn, "keys", {"living-room": 0.9})  # pure decay, no bump
    count = conn.execute(
        "SELECT count FROM failure_modes WHERE item_id='keys' AND zone_id='living-room'"
    ).fetchone()[0]
    assert count == 1


# --- adjacency ----------------------------------------------------------------


def test_adjacency_map_is_undirected(conn, home):
    adj = db.adjacency_map(conn)
    assert set(adj["hallway"]) == {"kitchen", "living-room"}
    assert adj["kitchen"] == ["hallway"]
    assert adj["living-room"] == ["hallway"]


# --- timeline (the sacred interface) ------------------------------------------


def test_timeline_round_trips_as_dwell_entries(conn, home):
    a = NOW - timedelta(hours=2)
    b = NOW - timedelta(hours=1)
    db.add_dwell_entry(conn, "living-room", a, b)
    db.add_dwell_entry(conn, "kitchen", b, NOW, source="quicklog")
    timeline = db.read_timeline(conn)
    assert timeline == [
        DwellEntry(zone_id="living-room", enter=a, exit=b),
        DwellEntry(zone_id="kitchen", enter=b, exit=NOW),
    ]


def test_add_dwell_entry_normalizes_bad_source(conn, home):
    db.add_dwell_entry(conn, "kitchen", NOW - timedelta(hours=1), NOW, source="sensor")
    src = conn.execute("SELECT source FROM dwell_entries").fetchone()[0]
    assert src == "retrospective"


def test_recent_dwell_entries_orders_by_end_and_carries_name_and_source(conn, home):
    db.add_dwell_entry(conn, "living-room", NOW - timedelta(hours=3), NOW - timedelta(hours=2))
    db.add_dwell_entry(conn, "kitchen", NOW - timedelta(hours=1), NOW, source="quicklog")
    rows = db.recent_dwell_entries(conn, limit=10)
    # Most recently ended first; zone name + source come back for display labelling.
    assert [(r["zone_id"], r["zone_name"], r["source"]) for r in rows] == [
        ("kitchen", "Kitchen", "quicklog"),
        ("living-room", "Living Room", "retrospective"),
    ]


def test_recent_dwell_entries_respects_limit(conn, home):
    for i in range(5):
        start = NOW - timedelta(hours=5 - i)
        db.add_dwell_entry(conn, "kitchen", start, start + timedelta(minutes=30))
    assert len(db.recent_dwell_entries(conn, limit=3)) == 3


# --- searches -----------------------------------------------------------------


def test_create_and_get_search(conn, home):
    anchor = NOW - timedelta(hours=3)
    sid = db.create_search(
        conn, "keys", anchor_claim_text="after lunch", anchor_time=anchor, now=NOW
    )
    s = db.get_search(conn, sid)
    assert s.item_id == "keys"
    assert s.anchor_claim_text == "after lunch"
    assert s.anchor_time == anchor
    assert s.status == "open" and s.followed_up is False
    assert s.created_at == NOW


def test_search_status_advances_and_followup(conn, home):
    sid = db.create_search(conn, "keys", anchor_claim_text=None, anchor_time=None, now=NOW)
    db.set_search_status(conn, sid, "found")
    assert db.get_search(conn, sid).status == "found"
    with pytest.raises(ValueError):
        db.set_search_status(conn, sid, "lost")
    db.mark_followed_up(conn, sid)
    assert db.get_search(conn, sid).followed_up is True


def test_next_followup_picks_oldest_open_unfollowed(conn, home):
    s1 = db.create_search(conn, "keys", anchor_claim_text=None, anchor_time=None, now=NOW)
    db.create_search(conn, "keys", anchor_claim_text=None, anchor_time=None, now=NOW)
    assert db.next_followup_search(conn).id == s1  # oldest first
    db.mark_followed_up(conn, s1)
    assert db.next_followup_search(conn).id == s1 + 1  # next one
    db.set_search_status(conn, s1 + 1, "found")  # resolved -> no longer open
    assert db.next_followup_search(conn) is None


def test_searches_are_append_only(conn, home):
    sid = db.create_search(conn, "keys", anchor_claim_text=None, anchor_time=None, now=NOW)
    with pytest.raises(Exception):
        conn.execute("DELETE FROM searches WHERE id = ?", (sid,))


# --- suggestions --------------------------------------------------------------


def test_suggestions_add_reject_and_count(conn, home):
    sid = db.create_search(conn, "keys", anchor_claim_text=None, anchor_time=None, now=NOW)
    s1 = db.add_suggestion(conn, sid, "kitchen", 1, surface_id="kitchen-counter", reason="home")
    db.add_suggestion(conn, sid, "living-room", 2, reason="dwelled")
    db.reject_suggestion(conn, s1)
    rows = db.list_suggestions(conn, sid)
    assert [(r["zone_id"], r["rank"], r["rejected"]) for r in rows] == [
        ("kitchen", 1, 1),
        ("living-room", 2, 0),
    ]
    assert db.count_rejected_suggestions(conn, sid) == 1
    assert db.get_suggestion(conn, s1)["surface_id"] == "kitchen-counter"


# --- finds + memory log (append-only) -----------------------------------------


def test_record_find_persists_metrics(conn, home):
    sid = db.create_search(conn, "keys", anchor_claim_text=None, anchor_time=None, now=NOW)
    fid = db.record_find(
        conn, sid, "living-room", surface_id=None, was_suggested_rank=2, places_checked=3, now=NOW
    )
    row = conn.execute("SELECT * FROM finds WHERE id = ?", (fid,)).fetchone()
    assert row["zone_id"] == "living-room"
    assert row["was_suggested_rank"] == 2 and row["places_checked"] == 3


def test_finds_are_append_only(conn, home):
    sid = db.create_search(conn, "keys", anchor_claim_text=None, anchor_time=None, now=NOW)
    fid = db.record_find(conn, sid, "living-room", now=NOW)
    with pytest.raises(Exception):
        conn.execute("UPDATE finds SET zone_id='kitchen' WHERE id=?", (fid,))
    with pytest.raises(Exception):
        conn.execute("DELETE FROM finds WHERE id=?", (fid,))


def test_list_finds_is_chronological_with_names_and_metrics(conn, home):
    s1 = db.create_search(conn, "keys", anchor_claim_text=None, anchor_time=None, now=NOW)
    s2 = db.create_search(conn, "keys", anchor_claim_text=None, anchor_time=None, now=NOW)
    db.record_find(
        conn, s1, "kitchen", was_suggested_rank=1, places_checked=1,
        now=NOW - timedelta(hours=1),
    )
    db.record_find(
        conn, s2, "living-room", was_suggested_rank=2, places_checked=2, now=NOW
    )
    finds = db.list_finds(conn)
    assert [(f["item_name"], f["zone_name"], f["was_suggested_rank"], f["places_checked"])
            for f in finds] == [
        ("Keys", "Kitchen", 1, 1),
        ("Keys", "Living Room", 2, 2),
    ]


def test_list_finds_empty_when_no_finds(conn, home):
    assert db.list_finds(conn) == []


def test_memory_log_is_silent_and_append_only(conn, home):
    sid = db.create_search(conn, "keys", anchor_claim_text=None, anchor_time=None, now=NOW)
    mid = db.log_memory(
        conn, sid, claimed_anchor="kitchen", actual_outcome="couch", now=NOW
    )
    with pytest.raises(Exception):
        conn.execute("UPDATE memory_log SET actual_outcome='x' WHERE id=?", (mid,))
    with pytest.raises(Exception):
        conn.execute("DELETE FROM memory_log WHERE id=?", (mid,))
