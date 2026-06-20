"""Tests for the onboarding-facing DB access layer (writes + reads).

Uses an in-memory database; reads come back as engine dataclasses, the mapping the
engine speaks in.
"""

import pytest

from wwiw import db
from wwiw.engine.types import Item, Surface, Zone


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


# --- zones --------------------------------------------------------------------


def test_create_zone_slugifies_and_returns_id(conn):
    zid = db.create_zone(conn, "Living Room")
    assert zid == "living-room"
    assert db.get_zone(conn, zid) == Zone(id="living-room", name="Living Room", kind="dwell")


def test_create_zone_dedupes_clashing_slugs(conn):
    a = db.create_zone(conn, "Bedroom")
    b = db.create_zone(conn, "bedroom")
    assert a == "bedroom"
    assert b == "bedroom-2"


def test_create_zone_normalizes_bad_kind_and_keeps_transit(conn):
    t = db.create_zone(conn, "Hallway", kind="transit")
    bad = db.create_zone(conn, "Nook", kind="lounge")
    assert db.get_zone(conn, t).kind == "transit"
    assert db.get_zone(conn, bad).kind == "dwell"


def test_list_dwell_zones_excludes_transit(conn):
    db.create_zone(conn, "Kitchen")
    db.create_zone(conn, "Hallway", kind="transit")
    assert [z.name for z in db.list_dwell_zones(conn)] == ["Kitchen"]
    assert {z.name for z in db.list_zones(conn)} == {"Kitchen", "Hallway"}


def test_get_zone_by_name_is_case_insensitive(conn):
    db.create_zone(conn, "Den")
    assert db.get_zone_by_name(conn, "den").id == "den"
    assert db.get_zone_by_name(conn, "missing") is None


# --- edges --------------------------------------------------------------------


def test_add_edge_is_undirected_and_idempotent(conn):
    a = db.create_zone(conn, "Kitchen")
    b = db.create_zone(conn, "Hallway", kind="transit")
    db.add_edge(conn, b, a)
    db.add_edge(conn, a, b)  # reversed + duplicate
    assert db.list_edges(conn) == [("hallway", "kitchen")]  # stored sorted, one row


def test_add_edge_ignores_self_loop(conn):
    a = db.create_zone(conn, "Kitchen")
    db.add_edge(conn, a, a)
    assert db.list_edges(conn) == []


def test_edge_requires_existing_zones(conn):
    a = db.create_zone(conn, "Kitchen")
    with pytest.raises(Exception):  # FK violation
        db.add_edge(conn, a, "ghost-zone")


# --- surfaces -----------------------------------------------------------------


def test_create_surface_records_source_and_lists(conn):
    z = db.create_zone(conn, "Kitchen")
    sid = db.create_surface(conn, z, "Counter", source="photo")
    db.create_surface(conn, z, "Table")  # default manual
    surfaces = db.list_surfaces(conn, z)
    assert surfaces == [
        Surface(id=sid, zone_id="kitchen", name="Counter", source="photo"),
        Surface(id="kitchen-table", zone_id="kitchen", name="Table", source="manual"),
    ]


def test_create_surface_normalizes_bad_source(conn):
    z = db.create_zone(conn, "Kitchen")
    sid = db.create_surface(conn, z, "Shelf", source="bogus")
    assert db.list_surfaces(conn, z)[0].source == "manual"


# --- items + priors -----------------------------------------------------------


def test_create_item_and_list(conn):
    z = db.create_zone(conn, "Kitchen")
    iid = db.create_item(conn, "Keys", home_zone_id=z)
    assert iid == "keys"
    assert db.list_items(conn) == [Item(id="keys", name="Keys", home_zone_id="kitchen")]


def test_set_prior_upserts(conn):
    z = db.create_zone(conn, "Kitchen")
    iid = db.create_item(conn, "Keys", home_zone_id=z)
    db.set_prior(conn, iid, z, 0.4)
    db.set_prior(conn, iid, z, 0.9)  # update, not duplicate
    rows = conn.execute("SELECT zone_id, weight FROM priors WHERE item_id = ?", (iid,)).fetchall()
    assert [(r["zone_id"], r["weight"]) for r in rows] == [("kitchen", 0.9)]


# --- counts (onboarding progress) ---------------------------------------------


def test_counts_reflect_onboarding_progress(conn):
    assert db.count_zones(conn) == 0 and db.count_items(conn) == 0
    z = db.create_zone(conn, "Kitchen")
    db.create_item(conn, "Keys", home_zone_id=z)
    assert db.count_zones(conn) == 1 and db.count_items(conn) == 1
