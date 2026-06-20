"""Tests for onboarding step 1: rooms (parse -> review -> confirm/persist)."""

import json

from wwiw import db

RESIDENCE_JSON = json.dumps(
    {
        "zones": [
            {"name": "Kitchen", "kind": "dwell"},
            {"name": "Hallway", "kind": "transit"},
            {"name": "Living Room", "kind": "dwell"},
        ],
        "edges": [["Kitchen", "Hallway"], ["Hallway", "Living Room"]],
    }
)


def _conn(client):
    return db.connect(client.app.state.db_path)


def test_rooms_form_renders(make_app):
    client = make_app()
    resp = client.get("/onboarding/rooms")
    assert resp.status_code == 200
    assert "Describe your home" in resp.text
    assert 'name="description"' in resp.text


def test_rooms_parse_with_model_shows_review(make_app):
    client = make_app(responses={"parse_residence": RESIDENCE_JSON})
    resp = client.post("/onboarding/rooms/parse", data={"description": "kitchen off the hall, living room too"})
    assert resp.status_code == 200
    # Parsed rooms appear as editable fields, edges as checkboxes.
    assert 'value="Kitchen"' in resp.text
    assert 'value="Living Room"' in resp.text
    assert "Kitchen||Hallway" in resp.text
    assert client.llm.calls[0]["task"] == "parse_residence"


def test_rooms_parse_offline_falls_back_to_line_split(make_app):
    client = make_app(available=False)
    resp = client.post("/onboarding/rooms/parse", data={"description": "Kitchen\nBedroom\n"})
    assert resp.status_code == 200
    assert "isn't running" in resp.text  # offline notice
    assert 'value="Kitchen"' in resp.text
    assert 'value="Bedroom"' in resp.text
    assert client.llm.calls == []  # model never called when unavailable


def test_rooms_confirm_persists_zones_and_edges(make_app):
    client = make_app()
    resp = client.post(
        "/onboarding/rooms/confirm",
        data={
            "zone_name": ["Kitchen", "Hallway", "Living Room"],
            "zone_kind": ["dwell", "transit", "dwell"],
            "edge": ["Kitchen||Hallway", "Hallway||Living Room"],
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/onboarding/photos"

    conn = _conn(client)
    zones = {z.name: z.kind for z in db.list_zones(conn)}
    assert zones == {"Kitchen": "dwell", "Hallway": "transit", "Living Room": "dwell"}
    assert db.list_edges(conn) == [("hallway", "kitchen"), ("hallway", "living-room")]
    conn.close()


def test_rooms_confirm_drops_blank_names_and_dangling_edges(make_app):
    client = make_app()
    client.post(
        "/onboarding/rooms/confirm",
        data={
            "zone_name": ["Kitchen", ""],  # second cleared -> dropped
            "zone_kind": ["dwell", "dwell"],
            "edge": ["Kitchen||Ghost"],  # endpoint dropped -> edge skipped
        },
        follow_redirects=False,
    )
    conn = _conn(client)
    assert [z.name for z in db.list_zones(conn)] == ["Kitchen"]
    assert db.list_edges(conn) == []
    conn.close()
