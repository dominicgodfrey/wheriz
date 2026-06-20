"""Stats view (M5): trending the find history.

Two layers: the pure ``summarize_finds`` roll-up (tested with plain dicts, no DB or
browser) and the rendered page (driven through the web layer). The view is reporting
only — it must never surface the silent memory-trust log.
"""

from datetime import datetime, timedelta

from wwiw import db
from wwiw.web.stats import summarize_finds


def _f(item, zone, places, rank=None):
    """A find row as the summary helper reads it (mirrors db.list_finds columns)."""
    return {
        "item_name": item,
        "zone_name": zone,
        "places_checked": places,
        "was_suggested_rank": rank,
    }


# --- pure summary -------------------------------------------------------------


def test_summary_empty():
    s = summarize_finds([])
    assert s.total == 0 and s.trend == "none" and s.rows == []


def test_summary_too_few_for_trend():
    s = summarize_finds([_f("Keys", "Kitchen", 1), _f("Keys", "Couch", 2)])
    assert s.total == 2 and s.trend == "early"
    assert s.avg_places == 1.5


def test_summary_improving_when_recent_checks_fewer():
    finds = [
        _f("Keys", "Couch", 4),
        _f("Keys", "Couch", 3),
        _f("Keys", "Couch", 1),
        _f("Keys", "Couch", 1),
    ]
    s = summarize_finds(finds)
    assert s.trend == "improving"
    assert s.early_avg == 3.5 and s.recent_avg == 1.0


def test_summary_slipping_when_recent_checks_more():
    finds = [_f("K", "C", 1), _f("K", "C", 1), _f("K", "C", 3), _f("K", "C", 4)]
    assert summarize_finds(finds).trend == "slipping"


def test_summary_steady_when_flat():
    finds = [_f("K", "C", 2)] * 4
    assert summarize_finds(finds).trend == "steady"


def test_summary_bar_scales_to_busiest_find():
    finds = [_f("K", "C", 1), _f("K", "C", 4)]
    rows = summarize_finds(finds).rows
    assert rows[0]["bar_pct"] == 25 and rows[1]["bar_pct"] == 100


# --- rendered page ------------------------------------------------------------


def _seed_finds(client, places_sequence):
    """Append finds with given places_checked values via the DB boundary, oldest first."""
    conn = db.connect(client.app.state.db_path)
    db.create_zone(conn, "Kitchen")
    db.create_zone(conn, "Living Room")
    db.create_item(conn, "Keys", home_zone_id="kitchen")
    base = datetime(2026, 6, 20, 9, 0, 0)
    for i, places in enumerate(places_sequence):
        sid = db.create_search(conn, "keys", anchor_claim_text=None, anchor_time=None, now=base)
        db.record_find(
            conn, sid, "living-room", was_suggested_rank=places, places_checked=places,
            now=base + timedelta(hours=i),
        )
    conn.close()


def test_stats_empty_state(make_app):
    client = make_app(available=False)
    r = client.get("/stats")
    assert r.status_code == 200
    assert "No finds yet" in r.text


def test_stats_renders_finds_and_improving_trend(make_app):
    client = make_app(available=False)
    _seed_finds(client, [4, 3, 1, 1])
    r = client.get("/stats")
    assert r.status_code == 200
    assert "things found" in r.text and ">4</strong>" in r.text
    assert "Keys" in r.text and "Living Room" in r.text
    assert "fewer" in r.text  # the improving-trend copy


def test_stats_documents_the_full_wipe(make_app):
    """The only reset is out-of-app (delete data/); the page must keep saying how."""
    client = make_app(available=False)
    r = client.get("/stats")
    assert "data" in r.text and "rm -rf data" in r.text


def test_stats_never_surfaces_memory_log(make_app):
    """A mismatch is logged silently; the stats page must not expose it as a score."""
    client = make_app(available=False)
    _seed_finds(client, [2, 2])
    conn = db.connect(client.app.state.db_path)
    db.log_memory(conn, 1, claimed_anchor="kitchen", actual_outcome="living-room|matched=False", now=datetime.now())
    conn.close()
    r = client.get("/stats")
    assert "matched=" not in r.text and "memory" not in r.text.lower()
