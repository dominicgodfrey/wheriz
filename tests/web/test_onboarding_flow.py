"""End-to-end onboarding: rooms -> photos -> items, leaving a consistent DB + a
landing page that reports the home as set up. Drives the confirm/save endpoints (the
parse steps are covered per-step) with a fake model, never touching Ollama.
"""

from wwiw import db
from wwiw.engine.scoring import first_pass_suggestion


def _conn(client):
    return db.connect(client.app.state.db_path)


def test_full_wizard_persists_a_usable_home(make_app):
    client = make_app()

    # Step 1: rooms + a connection.
    r = client.post(
        "/onboarding/rooms/confirm",
        data={
            "zone_name": ["Kitchen", "Hallway", "Living Room"],
            "zone_kind": ["dwell", "transit", "dwell"],
            "edge": ["Kitchen||Hallway", "Hallway||Living Room"],
        },
        follow_redirects=False,
    )
    assert r.headers["location"] == "/onboarding/photos"

    # Step 2: surfaces for the kitchen.
    client.post(
        "/onboarding/photos/kitchen/save",
        data={"surface": ["counter"], "manual_text": "table"},
        follow_redirects=False,
    )

    # Step 3: items + seeded home priors.
    r = client.post(
        "/onboarding/items/confirm",
        data={
            "item_name": ["keys", "wallet"],
            "item_home": ["kitchen", "living-room"],
            "item_confidence": ["0.5", "0.6"],
        },
        follow_redirects=False,
    )
    assert r.headers["location"] == "/onboarding/done"

    # The persisted state is internally consistent.
    conn = _conn(client)
    assert {z.name for z in db.list_zones(conn)} == {"Kitchen", "Hallway", "Living Room"}
    assert db.list_edges(conn) == [("hallway", "kitchen"), ("hallway", "living-room")]
    assert {s.name for s in db.list_surfaces(conn, "kitchen")} == {"counter", "table"}
    items_by_name = {i.name: i for i in db.list_items(conn)}
    assert {n: i.home_zone_id for n, i in items_by_name.items()} == {
        "keys": "kitchen",
        "wallet": "living-room",
    }

    # The seeded prior is shaped so the engine's first-pass suggestion finds the home.
    keys_priors = {
        r["zone_id"]: r["weight"]
        for r in conn.execute("SELECT zone_id, weight FROM priors WHERE item_id = 'keys'")
    }
    conn.close()
    suggestion = first_pass_suggestion(items_by_name["keys"], priors=keys_priors)
    assert suggestion is not None and suggestion.zone_id == "kitchen"

    # Landing page now reflects a set-up home.
    body = client.get("/").text
    assert "You're set up" in body
    assert "3 rooms" in body and "2 items" in body
