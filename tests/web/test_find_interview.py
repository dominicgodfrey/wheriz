"""Find loop, step 2: rejecting the usual spot -> retrospective timeline interview ->
engine-ranked suggestions.

Drives the real ``rank_zones`` through the web layer. Most assertions run with the model
offline so the warm fallback reasons are deterministic; one test scripts ``phrase_reason``
to prove the online phrasing path is wired (the LLM words, the engine ranks).
"""

import json

from wwiw import db


def _seed(client):
    """Kitchen (keys' home), two dwell rooms with a couch surface, a transit hallway."""
    conn = db.connect(client.app.state.db_path)
    db.create_zone(conn, "Kitchen")
    db.create_zone(conn, "Living Room")
    db.create_zone(conn, "Study")
    db.create_zone(conn, "Hallway", kind="transit")
    db.add_edge(conn, "kitchen", "hallway")
    db.create_surface(conn, "living-room", "Couch")
    db.create_item(conn, "Keys", home_zone_id="kitchen")
    db.set_prior(conn, "keys", "kitchen", 0.6)
    conn.close()


def _open_search(client):
    """Run the real query step so a home suggestion is recorded, return the search id."""
    client.llm.responses = {
        "parse_search_query": json.dumps(
            {"item": "keys", "anchor_text": None, "anchor_time": None}
        )
    }
    client.post("/find", data={"query": "keys"})
    return 1


def test_reject_home_rules_out_usual_spot_and_starts_retrace(make_app):
    client = make_app()
    _seed(client)
    sid = _open_search(client)

    r = client.post(f"/find/{sid}/reject-home", follow_redirects=False)
    assert r.status_code == 200
    body = r.text
    assert "Where have you been?" in body
    # The retrace offers the other dwell rooms, never the ruled-out home or transit spaces.
    assert "Living Room" in body and "Study" in body
    assert "Kitchen" not in body and "Hallway" not in body

    conn = db.connect(client.app.state.db_path)
    home_sugg = db.list_suggestions(conn, sid)
    conn.close()
    assert home_sugg[0]["zone_id"] == "kitchen" and home_sugg[0]["rejected"] == 1


def test_timeline_with_no_pick_reasks(make_app):
    client = make_app()
    _seed(client)
    sid = _open_search(client)
    client.post(f"/find/{sid}/reject-home")
    r = client.post(f"/find/{sid}/timeline", data={}, follow_redirects=False)
    assert r.status_code == 200
    assert "Pick at least one room" in r.text


def test_timeline_ranks_by_dwell_and_persists_suggestions(make_app):
    client = make_app(available=False)  # offline -> deterministic fallback reasons
    _seed(client)
    # Offline query resolves "keys" by substring; home suggestion recorded at rank 1.
    client.post("/find", data={"query": "keys"})
    client.post("/find/1/reject-home")

    r = client.post(
        "/find/1/timeline",
        data={"zone": ["living-room", "study"], "dwell_living-room": "long", "dwell_study": "brief"},
        follow_redirects=False,
    )
    assert r.status_code == 200
    body = r.text
    # More dwell in the living room -> it ranks above the study.
    assert body.index("Living Room") < body.index("Study")
    assert "good chance" in body  # warm fallback phrasing (apostrophe is HTML-escaped)
    assert "maybe on the Couch" in body  # surface hint on the top suggestion only

    conn = db.connect(client.app.state.db_path)
    sugg = db.list_suggestions(conn, 1)
    conn.close()
    # Home was rank 1 (rejected); the retrace suggestions continue after it.
    assert [(s["zone_id"], s["rank"], s["rejected"]) for s in sugg] == [
        ("kitchen", 1, 1),
        ("living-room", 2, 0),
        ("study", 3, 0),
    ]
    # Synthesized occupancy entered the (sacred) timeline.
    conn = db.connect(client.app.state.db_path)
    zones_logged = {e.zone_id for e in db.read_timeline(conn)}
    conn.close()
    assert zones_logged == {"living-room", "study"}


def test_failure_memory_outranks_more_dwell(make_app):
    client = make_app(available=False)
    _seed(client)
    conn = db.connect(client.app.state.db_path)
    # Keys have a strong history of turning up in the study when not in the kitchen.
    db.write_failure_modes(conn, "keys", {"study": 6.0}, bumped_zone_id="study")
    conn.close()

    client.post("/find", data={"query": "keys"})
    client.post("/find/1/reject-home")
    r = client.post(
        "/find/1/timeline",
        data={"zone": ["living-room", "study"], "dwell_living-room": "long", "dwell_study": "brief"},
    )
    body = r.text
    # Despite far less dwell, failure-mode memory floats the study to the top.
    assert body.index("Study") < body.index("Living Room")


def test_transit_zone_never_suggested_via_adjacency(make_app):
    client = make_app(available=False)
    _seed(client)  # kitchen (home) is adjacent to the hallway (transit)
    client.post("/find", data={"query": "keys"})
    client.post("/find/1/reject-home")
    r = client.post(
        "/find/1/timeline",
        data={"zone": ["living-room"], "dwell_living-room": "while"},
    )
    # Hallway is adjacent to the claimed home but it's transit — never a place to look.
    conn = db.connect(client.app.state.db_path)
    zones = {s["zone_id"] for s in db.list_suggestions(conn, 1) if not s["rejected"]}
    conn.close()
    assert "hallway" not in zones
    assert zones == {"living-room"}


def test_online_phrase_reason_is_used(make_app):
    client = make_app()  # available -> online phrasing path
    _seed(client)
    client.llm.responses = {
        "parse_search_query": json.dumps({"item": "keys", "anchor_text": None, "anchor_time": None}),
        "phrase_reason": "It could well be in there — worth a peek.",
    }
    client.post("/find", data={"query": "keys"})
    client.post("/find/1/reject-home")
    r = client.post("/find/1/timeline", data={"zone": ["living-room"], "dwell_living-room": "while"})
    assert "It could well be in there — worth a peek." in r.text
    # The model was asked to phrase, grounded in the engine's reason.
    phrasings = [c for c in client.llm.calls if c["task"] == "phrase_reason"]
    assert phrasings and "Living Room" in phrasings[0]["prompt"]
