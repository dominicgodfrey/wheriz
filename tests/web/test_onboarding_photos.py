"""Tests for onboarding step 2: photos -> surface extraction -> prune -> persist."""

import json

from wwiw import db

SURFACES_JSON = json.dumps({"surfaces": ["kitchen counter", "kitchen table"]})


def _conn(client):
    return db.connect(client.app.state.db_path)


def _seed_zone(client, name, kind="dwell"):
    conn = _conn(client)
    zid = db.create_zone(conn, name, kind)
    conn.close()
    return zid


def test_photos_lists_only_dwell_zones(make_app):
    client = make_app()
    _seed_zone(client, "Kitchen", "dwell")
    _seed_zone(client, "Hallway", "transit")
    resp = client.get("/onboarding/photos")
    assert resp.status_code == 200
    assert "Kitchen" in resp.text
    assert "Hallway" not in resp.text  # transit rooms hold no items
    assert "/onboarding/items" in resp.text  # continue link


def test_photos_extract_with_model_shows_detected(make_app):
    client = make_app(responses={"extract_surfaces": SURFACES_JSON})
    zid = _seed_zone(client, "Kitchen")
    resp = client.post(
        f"/onboarding/photos/{zid}",
        files={"photo": ("kitchen.jpg", b"PNGDATA", "image/jpeg")},
    )
    assert resp.status_code == 200
    assert 'value="kitchen counter"' in resp.text
    assert 'value="kitchen table"' in resp.text
    call = client.llm.calls[0]
    assert call["kind"] == "vision" and call["task"] == "extract_surfaces"
    assert call["images"] == [b"PNGDATA"]


def test_photos_extract_offline_shows_notice_and_skips_model(make_app):
    client = make_app(available=False)
    zid = _seed_zone(client, "Kitchen")
    resp = client.post(
        f"/onboarding/photos/{zid}",
        files={"photo": ("kitchen.jpg", b"PNGDATA", "image/jpeg")},
    )
    assert resp.status_code == 200
    assert "isn't running" in resp.text
    assert client.llm.calls == []


def test_photos_save_persists_detected_and_manual_with_sources(make_app):
    client = make_app()
    zid = _seed_zone(client, "Kitchen")
    resp = client.post(
        f"/onboarding/photos/{zid}/save",
        data={"surface": ["kitchen counter", "kitchen table"], "manual_text": "bookshelf\nwindowsill\n"},
        follow_redirects=False,
    )
    assert resp.status_code == 303 and resp.headers["location"] == "/onboarding/photos"

    conn = _conn(client)
    surfaces = {s.name: s.source for s in db.list_surfaces(conn, zid)}
    conn.close()
    assert surfaces == {
        "kitchen counter": "photo",
        "kitchen table": "photo",
        "bookshelf": "manual",
        "windowsill": "manual",
    }


def test_photos_save_dedupes_against_existing(make_app):
    client = make_app()
    zid = _seed_zone(client, "Kitchen")
    conn = _conn(client)
    db.create_surface(conn, zid, "Counter", source="manual")
    conn.close()

    client.post(
        f"/onboarding/photos/{zid}/save",
        data={"surface": ["Counter"], "manual_text": "Counter\nShelf"},
        follow_redirects=False,
    )
    conn = _conn(client)
    names = sorted(s.name for s in db.list_surfaces(conn, zid))
    conn.close()
    assert names == ["Counter", "Shelf"]  # Counter not duplicated


def test_photos_save_unknown_zone_redirects(make_app):
    client = make_app()
    resp = client.post(
        "/onboarding/photos/ghost/save", data={"manual_text": "x"}, follow_redirects=False
    )
    assert resp.status_code == 303
