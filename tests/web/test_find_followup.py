"""Find loop, the tail: the single next-app-open follow-up for an open search.

An unresolved search surfaces once on the landing page ("did your X turn up?"). Whatever
the answer, it's marked followed up so we never pester twice — "found" routes to recording
where (closing it with learning), "not yet" lets it quietly expire.
"""

from wwiw import db


def _seed(client):
    conn = db.connect(client.app.state.db_path)
    db.create_zone(conn, "Kitchen")
    db.create_zone(conn, "Living Room")
    db.create_item(conn, "Keys", home_zone_id="kitchen")
    db.set_prior(conn, "keys", "kitchen", 0.6)
    conn.close()


def _open_search(client):
    """Start a search and walk away without confirming -> it stays open + un-followed."""
    client.post("/find", data={"query": "keys"})


def _conn(client):
    return db.connect(client.app.state.db_path)


def test_open_search_surfaces_a_followup_on_landing(make_app):
    client = make_app(available=False)
    _seed(client)
    assert "Earlier you were looking for" not in client.get("/").text  # nothing pending yet

    _open_search(client)
    body = client.get("/").text
    assert "Earlier you were looking for" in body
    assert "Keys" in body and "/find/1/followup" in body


def test_not_yet_expires_and_stops_asking(make_app):
    client = make_app(available=False)
    _seed(client)
    _open_search(client)

    r = client.post("/find/1/followup", data={"outcome": "gone"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"

    conn = _conn(client)
    search = db.get_search(conn, 1)
    conn.close()
    assert search.status == "expired" and search.followed_up is True
    assert "Earlier you were looking for" not in client.get("/").text  # asked once, done


def test_found_routes_to_record_then_learns(make_app):
    client = make_app(available=False)
    _seed(client)
    _open_search(client)

    r = client.post("/find/1/followup", data={"outcome": "found"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/find/1/where"

    where = client.get("/find/1/where")
    assert where.status_code == 200 and "Where did your Keys turn up?" in where.text

    done = client.post("/find/1/confirm", data={"zone_id": "living-room"})
    assert "Found it" in done.text
    conn = _conn(client)
    assert db.get_search(conn, 1).status == "found"
    assert conn.execute("SELECT zone_id FROM finds").fetchone()[0] == "living-room"
    conn.close()
    # And the banner is gone afterward.
    assert "Earlier you were looking for" not in client.get("/").text


def test_followup_marks_once_even_before_recording(make_app):
    client = make_app(available=False)
    _seed(client)
    _open_search(client)
    # Say "found" (so it's still open, awaiting the where-form) — it must not re-prompt.
    client.post("/find/1/followup", data={"outcome": "found"})
    conn = _conn(client)
    assert db.get_search(conn, 1).followed_up is True
    conn.close()
    assert "Earlier you were looking for" not in client.get("/").text


def test_resolved_search_never_prompts(make_app):
    client = make_app(available=False)
    _seed(client)
    client.post("/find", data={"query": "keys"})
    client.post("/find/1/confirm", data={"zone_id": "kitchen"})  # found right away
    assert "Earlier you were looking for" not in client.get("/").text


def test_followup_on_missing_or_resolved_search_redirects(make_app):
    client = make_app(available=False)
    _seed(client)
    r = client.post("/find/999/followup", data={"outcome": "found"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
