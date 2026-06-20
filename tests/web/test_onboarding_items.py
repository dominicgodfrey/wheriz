"""Tests for onboarding step 3: loss interview -> items + seeded priors."""

import json

from wwiw import db

ITEMS_JSON = json.dumps(
    {
        "items": [
            {"name": "keys", "home_zone": "Kitchen", "confidence": 0.5},
            {"name": "wallet", "home_zone": "Entrance", "confidence": 0.6},
        ]
    }
)


def _conn(client):
    return db.connect(client.app.state.db_path)


def _seed_zones(client):
    conn = _conn(client)
    db.create_zone(conn, "Kitchen", "dwell")
    db.create_zone(conn, "Entrance", "transit")
    conn.close()


def test_items_form_renders(make_app):
    client = make_app()
    resp = client.get("/onboarding/items")
    assert resp.status_code == 200
    assert "misplace most often" in resp.text


def test_items_parse_with_model_prefills_home(make_app):
    client = make_app(responses={"parse_loss_interview": ITEMS_JSON})
    _seed_zones(client)
    resp = client.post("/onboarding/items/parse", data={"q1": "keys and wallet", "q2": "", "q3": ""})
    assert resp.status_code == 200
    assert 'value="keys"' in resp.text and 'value="wallet"' in resp.text
    # Kitchen option is pre-selected for the keys row's home zone.
    assert '<option value="kitchen" selected>Kitchen</option>' in resp.text
    assert client.llm.calls[0]["task"] == "parse_loss_interview"


def test_items_parse_offline_lists_bare_items(make_app):
    client = make_app(available=False)
    _seed_zones(client)
    resp = client.post("/onboarding/items/parse", data={"q1": "keys, wallet, phone"})
    assert resp.status_code == 200
    for name in ("keys", "wallet", "phone"):
        assert f'value="{name}"' in resp.text
    assert client.llm.calls == []


def test_items_confirm_persists_items_and_priors(make_app):
    client = make_app()
    _seed_zones(client)
    resp = client.post(
        "/onboarding/items/confirm",
        data={
            "item_name": ["keys", "wallet"],
            "item_home": ["kitchen", ""],  # wallet: no home chosen
            "item_confidence": ["0.5", "0.6"],
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303 and resp.headers["location"] == "/onboarding/done"

    conn = _conn(client)
    items = {i.name: i.home_zone_id for i in db.list_items(conn)}
    assert items == {"keys": "kitchen", "wallet": None}
    priors = conn.execute("SELECT item_id, zone_id, weight FROM priors").fetchall()
    conn.close()
    # Only keys (with a home) seeds a prior; wallet seeds none.
    assert [(r["item_id"], r["zone_id"], r["weight"]) for r in priors] == [("keys", "kitchen", 0.5)]


def test_items_confirm_ignores_invalid_home(make_app):
    client = make_app()
    _seed_zones(client)
    client.post(
        "/onboarding/items/confirm",
        data={"item_name": ["keys"], "item_home": ["ghost-zone"], "item_confidence": ["0.5"]},
        follow_redirects=False,
    )
    conn = _conn(client)
    assert db.list_items(conn)[0].home_zone_id is None
    assert conn.execute("SELECT COUNT(*) FROM priors").fetchone()[0] == 0
    conn.close()


def test_items_confirm_drops_blank_names(make_app):
    client = make_app()
    _seed_zones(client)
    client.post(
        "/onboarding/items/confirm",
        data={"item_name": ["keys", ""], "item_home": ["kitchen", "kitchen"], "item_confidence": ["0.5", "0.5"]},
        follow_redirects=False,
    )
    conn = _conn(client)
    assert [i.name for i in db.list_items(conn)] == ["keys"]
    conn.close()


def test_done_page_summarizes(make_app):
    client = make_app()
    _seed_zones(client)
    conn = _conn(client)
    db.create_item(conn, "keys", home_zone_id="kitchen")
    conn.close()
    resp = client.get("/onboarding/done")
    assert resp.status_code == 200
    assert "You're all set" in resp.text
    assert "1 thing" in resp.text  # singular, no plural 's'
