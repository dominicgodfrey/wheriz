"""Find loop, step 1: the query box and the first-pass (home-prior) suggestion.

Drives the real engine (``first_pass_suggestion``) through the web layer with a fake
model, asserting the parsed query opens a search and the home prior is offered first.
"""

import json

from wwiw import db


def _seed_home(client):
    """A small set-up home with a couch-homed item and a known surface."""
    conn = db.connect(client.app.state.db_path)
    db.create_zone(conn, "Kitchen")
    db.create_zone(conn, "Living Room")
    db.create_surface(conn, "living-room", "Couch")
    db.create_item(conn, "Keys", home_zone_id="living-room")
    db.set_prior(conn, "keys", "living-room", 0.6)
    conn.close()


def _query_response(item="keys", anchor_text="after dinner", anchor_time="2026-06-20T19:30:00"):
    return json.dumps({"item": item, "anchor_text": anchor_text, "anchor_time": anchor_time})


def test_query_form_blocks_when_no_items(make_app):
    client = make_app()
    body = client.get("/find").text
    assert "don't track anything yet" in body
    assert 'action="/find"' not in body  # no submit form offered


def test_query_form_lists_items_when_set_up(make_app):
    client = make_app()
    _seed_home(client)
    body = client.get("/find").text
    assert "What's missing?" in body
    assert "Keys" in body  # available in the picker


def test_query_resolves_item_and_offers_home_first(make_app):
    client = make_app()
    _seed_home(client)
    client.llm.responses = {"parse_search_query": _query_response()}

    r = client.post("/find", data={"query": "where are my keys?"}, follow_redirects=False)
    assert r.status_code == 200
    body = r.text
    assert "usually lives in" in body and "Living Room" in body
    assert "Couch" in body  # surface hint decorates the top suggestion
    assert "found it there" in body.lower()

    # A search was opened with the resolved anchor, and the home suggestion recorded.
    conn = db.connect(client.app.state.db_path)
    search = db.get_search(conn, 1)
    assert search.item_id == "keys" and search.status == "open"
    assert search.anchor_claim_text == "after dinner"
    sugg = db.list_suggestions(conn, 1)
    conn.close()
    assert [(s["zone_id"], s["rank"]) for s in sugg] == [("living-room", 1)]


def test_explicit_item_pick_overrides_parse(make_app):
    client = make_app()
    _seed_home(client)
    # Model would say "umbrella" but the user explicitly picked keys from the list.
    client.llm.responses = {"parse_search_query": _query_response(item="umbrella")}
    r = client.post(
        "/find", data={"query": "", "item_id": "keys"}, follow_redirects=False
    )
    assert r.status_code == 200
    assert "Your Keys" in r.text


def test_unresolved_item_reasks_with_picker(make_app):
    client = make_app()
    _seed_home(client)
    client.llm.responses = {"parse_search_query": _query_response(item="spaceship")}
    r = client.post("/find", data={"query": "my spaceship"}, follow_redirects=False)
    assert r.status_code == 200
    assert "couldn't tell what" in r.text
    # No search should have been opened for an unresolved item.
    conn = db.connect(client.app.state.db_path)
    assert conn.execute("SELECT COUNT(*) FROM searches").fetchone()[0] == 0
    conn.close()


def test_offline_query_falls_back_to_substring_match(make_app):
    client = make_app(available=False)  # model down
    _seed_home(client)
    r = client.post("/find", data={"query": "keys"}, follow_redirects=False)
    assert r.status_code == 200
    assert "Your Keys" in r.text
    # No model call was made.
    assert client.llm.calls == []


def test_item_without_home_skips_to_retrace(make_app):
    client = make_app()
    conn = db.connect(client.app.state.db_path)
    db.create_zone(conn, "Kitchen")
    db.create_item(conn, "Umbrella")  # no home zone / prior
    conn.close()
    client.llm.responses = {"parse_search_query": _query_response(item="umbrella", anchor_text=None, anchor_time=None)}
    r = client.post("/find", data={"query": "my umbrella"}, follow_redirects=False)
    assert r.status_code == 200
    assert "don't have a usual spot" in r.text
    assert "/reject-home" in r.text  # the retrace button is the only path
