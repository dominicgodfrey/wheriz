"""Find loop, step 3: confirming where the item turned up -> append-only find, learning
update, silent memory log, and a warm acknowledgment.

The keystone here is the canonical scenario end-to-end: repeatedly finding the keys away
from their usual spot makes that spot lead the next search — visible ranking improvement
through the real engine, driven entirely through the web layer with the model offline.
"""

from wwiw import db


def _seed(client):
    conn = db.connect(client.app.state.db_path)
    db.create_zone(conn, "Kitchen")
    db.create_zone(conn, "Living Room")
    db.create_item(conn, "Keys", home_zone_id="kitchen")
    db.set_prior(conn, "keys", "kitchen", 0.6)
    conn.close()


def _conn(client):
    return db.connect(client.app.state.db_path)


def _latest_search_id(client):
    conn = _conn(client)
    sid = conn.execute("SELECT MAX(id) FROM searches").fetchone()[0]
    conn.close()
    return sid


def _find_on_living_room(client):
    """One full away-from-home loop: query -> reject home -> retrace -> confirm couch."""
    client.post("/find", data={"query": "keys"})
    sid = _latest_search_id(client)
    client.post(f"/find/{sid}/reject-home")
    client.post(f"/find/{sid}/timeline", data={"zone": ["living-room"], "dwell_living-room": "long"})
    return client.post(f"/find/{sid}/confirm", data={"zone_id": "living-room"})


# --- home find (first pass) ---------------------------------------------------


def test_found_in_usual_spot_reinforces_home(make_app):
    client = make_app(available=False)
    _seed(client)
    client.post("/find", data={"query": "keys"})
    r = client.post("/find/1/confirm", data={"zone_id": "kitchen"}, follow_redirects=False)
    assert r.status_code == 200
    assert "Right where it usually lives" in r.text

    conn = _conn(client)
    find = conn.execute("SELECT * FROM finds").fetchone()
    assert find["zone_id"] == "kitchen" and find["was_suggested_rank"] == 1
    assert find["places_checked"] == 1  # only the usual spot was checked
    # Home prior reinforced, failure memory untouched (a home find isn't a "failure").
    assert db.read_priors(conn, "keys")["kitchen"] > 0.6
    assert db.read_failure_modes(conn, "keys") == {}
    assert db.get_search(conn, 1).status == "found"
    assert conn.execute("SELECT COUNT(*) FROM memory_log").fetchone()[0] == 1
    conn.close()


# --- away find (via a ranked suggestion) --------------------------------------


def test_found_via_suggestion_learns_failure_mode(make_app):
    client = make_app(available=False)
    _seed(client)
    r = _find_on_living_room(client)
    assert r.status_code == 200
    body = r.text
    assert "was in the" in body and "Living Room" in body

    conn = _conn(client)
    find = conn.execute("SELECT * FROM finds").fetchone()
    assert find["zone_id"] == "living-room"
    assert find["was_suggested_rank"] == 2  # home was rank 1, couch the next ranked
    assert find["places_checked"] == 2  # ruled out home, then found it
    # Prior shifted toward the couch; failure memory now records the away spot.
    priors = db.read_priors(conn, "keys")
    assert priors["living-room"] > 0 and priors["kitchen"] < 0.6
    fm = db.read_failure_modes(conn, "keys")
    assert fm.get("living-room", 0) > 0
    count = conn.execute(
        "SELECT count FROM failure_modes WHERE item_id='keys' AND zone_id='living-room'"
    ).fetchone()[0]
    assert count == 1
    # The silent memory log captured the mismatch (claimed home != actual couch).
    mem = conn.execute("SELECT actual_outcome FROM memory_log").fetchone()[0]
    assert "matched=False" in mem
    conn.close()


# --- none of these (free text) ------------------------------------------------


def test_none_of_these_creates_new_zone(make_app):
    client = make_app(available=False)
    _seed(client)
    client.post("/find", data={"query": "keys"})
    client.post("/find/1/reject-home")
    client.post("/find/1/timeline", data={"zone": ["living-room"], "dwell_living-room": "while"})
    r = client.post("/find/1/confirm", data={"other_zone": "The Car"}, follow_redirects=False)
    assert r.status_code == 200
    assert "The Car" in r.text

    conn = _conn(client)
    # The home grew a room so the loop could close, and the find points at it.
    assert db.get_zone_by_name(conn, "The Car") is not None
    find = conn.execute("SELECT * FROM finds").fetchone()
    assert find["zone_id"] == "the-car" and find["was_suggested_rank"] is None
    conn.close()


def test_none_of_these_matches_existing_zone(make_app):
    client = make_app(available=False)
    _seed(client)
    client.post("/find", data={"query": "keys"})
    client.post("/find/1/reject-home")
    client.post("/find/1/timeline", data={"zone": ["living-room"], "dwell_living-room": "while"})
    # Typing a known room name resolves to it rather than creating a duplicate.
    client.post("/find/1/confirm", data={"other_zone": "kitchen"})
    conn = _conn(client)
    assert conn.execute("SELECT COUNT(*) FROM zones WHERE id='kitchen'").fetchone()[0] == 1
    assert conn.execute("SELECT zone_id FROM finds").fetchone()[0] == "kitchen"
    conn.close()


# --- idempotence --------------------------------------------------------------


def test_confirm_on_resolved_search_does_not_double_record(make_app):
    client = make_app(available=False)
    _seed(client)
    client.post("/find", data={"query": "keys"})
    client.post("/find/1/confirm", data={"zone_id": "kitchen"})
    # A double submit (search already found) is ignored, not recorded twice.
    r = client.post("/find/1/confirm", data={"zone_id": "kitchen"}, follow_redirects=False)
    assert r.status_code == 303
    conn = _conn(client)
    assert conn.execute("SELECT COUNT(*) FROM finds").fetchone()[0] == 1
    conn.close()


# --- the canonical scenario: ranking improves ---------------------------------


def test_repeated_couch_finds_make_couch_lead_next_time(make_app):
    client = make_app(available=False)
    _seed(client)
    # Initially the usual spot is the kitchen.
    first = client.post("/find", data={"query": "keys"})
    assert "Kitchen" in first.text and "Living Room" not in first.text
    db.get_search(_conn(client), 1)  # search 1 opened

    _find_on_living_room(client)  # find #1 away from home
    _find_on_living_room(client)  # find #2 away from home

    # The prior has now tipped toward the couch...
    conn = _conn(client)
    priors = db.read_priors(conn, "keys")
    conn.close()
    assert priors["living-room"] > priors["kitchen"]

    # ...so the very next search offers the living room first, not the kitchen.
    again = client.post("/find", data={"query": "keys"})
    assert "usually lives in" in again.text and "Living Room" in again.text
