"""Quick dwell-log page (M5): the proactive half of the hybrid timeline stub.

What's logged here must land in the same timeline the find loop reads, tagged
``source=quicklog`` but otherwise indistinguishable to the engine. These tests drive the
page through the web layer (model offline throughout — the log never touches the LLM) and
assert the write hits the timeline and feeds ranking.
"""

from wwiw import db


def _seed(client):
    conn = db.connect(client.app.state.db_path)
    db.create_zone(conn, "Kitchen")
    db.create_zone(conn, "Living Room")
    db.create_zone(conn, "Hallway", kind="transit")
    conn.close()


def _conn(client):
    return db.connect(client.app.state.db_path)


# --- rendering ----------------------------------------------------------------


def test_log_form_lists_dwell_rooms_only(make_app):
    client = make_app(available=False)
    _seed(client)
    r = client.get("/log")
    assert r.status_code == 200
    assert "Kitchen" in r.text and "Living Room" in r.text
    # Transit spaces hold nothing, so they're not offered as a place you "settle".
    assert "Hallway" not in r.text


def test_log_form_without_rooms_points_to_onboarding(make_app):
    client = make_app(available=False)
    r = client.get("/log")
    assert r.status_code == 200
    assert "/onboarding/rooms" in r.text


# --- writing the timeline -----------------------------------------------------


def test_logging_appends_a_quicklog_dwell_entry(make_app):
    client = make_app(available=False)
    _seed(client)
    r = client.post("/log", data={"zone_id": "kitchen", "dwell": "long"})
    assert r.status_code == 200
    assert "Logged" in r.text and "Kitchen" in r.text

    conn = _conn(client)
    rows = conn.execute(
        "SELECT zone_id, source, enter, exit FROM dwell_entries"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["zone_id"] == "kitchen"
    assert rows[0]["source"] == "quicklog"
    assert rows[0]["enter"] < rows[0]["exit"]  # a real interval ending now
    conn.close()


def test_longer_dwell_spans_more_time(make_app):
    client = make_app(available=False)
    _seed(client)
    client.post("/log", data={"zone_id": "kitchen", "dwell": "brief"})
    client.post("/log", data={"zone_id": "living-room", "dwell": "long"})
    conn = _conn(client)
    spans = {}
    for row in conn.execute("SELECT zone_id, enter, exit FROM dwell_entries").fetchall():
        spans[row["zone_id"]] = db._from_iso(row["exit"]) - db._from_iso(row["enter"])
    conn.close()
    assert spans["living-room"] > spans["kitchen"]


def test_missing_room_does_not_write(make_app):
    client = make_app(available=False)
    _seed(client)
    r = client.post("/log", data={"zone_id": "", "dwell": "while"})
    assert r.status_code == 200
    assert "Pick a room" in r.text
    conn = _conn(client)
    assert conn.execute("SELECT COUNT(*) FROM dwell_entries").fetchone()[0] == 0
    conn.close()


def test_recent_panel_echoes_logged_entries(make_app):
    client = make_app(available=False)
    _seed(client)
    client.post("/log", data={"zone_id": "living-room", "dwell": "while"})
    r = client.get("/log")
    assert "Recently logged" in r.text
    assert "Living Room" in r.text and "quick-logged" in r.text


# --- the point of it: quick-logged dwell feeds the find loop ------------------


def test_quicklogged_dwell_is_read_by_the_engine_timeline(make_app):
    """A quick-logged stay shows up in read_timeline exactly like a retraced one."""
    client = make_app(available=False)
    _seed(client)
    client.post("/log", data={"zone_id": "living-room", "dwell": "long"})
    conn = _conn(client)
    timeline = db.read_timeline(conn)
    conn.close()
    # The engine consumes (zone, enter, exit) — source-agnostic, as the interface demands.
    assert len(timeline) == 1
    assert timeline[0].zone_id == "living-room"
