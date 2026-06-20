"""Tests for the five LLM edge tasks, driven by recorded (synthetic) model outputs.

A fake client returns captured Ollama response shapes — fenced JSON, prose-wrapped JSON,
nulls, sloppy values — so the parsing/normalization logic is exercised without a live
model (an architecture rule). Fixtures use only synthetic residence/item content.
"""

from datetime import datetime

import pytest

from wwiw.llm.client import LLMError
from wwiw.llm.tasks import (
    ParsedQuery,
    _extract_json,
    extract_surfaces,
    parse_loss_interview,
    parse_residence,
    parse_search_query,
    phrase_reason,
)


class FakeLLMClient:
    """Returns scripted text/vision responses and records how it was called."""

    def __init__(self, text_response="", vision_response=""):
        self.text_response = text_response
        self.vision_response = vision_response
        self.calls = []

    def generate_text(self, prompt, *, task, format=None, options=None):
        self.calls.append({"kind": "text", "task": task, "format": format, "prompt": prompt})
        return self.text_response

    def generate_vision(self, prompt, images, *, task, format=None, options=None):
        self.calls.append(
            {"kind": "vision", "task": task, "format": format, "prompt": prompt, "images": list(images)}
        )
        return self.vision_response


# --- _extract_json robustness -------------------------------------------------


def test_extract_json_plain_object():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_strips_code_fence():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_tolerates_surrounding_prose():
    text = 'Sure! Here you go:\n{"surfaces": ["counter"]}\nHope that helps.'
    assert _extract_json(text) == {"surfaces": ["counter"]}


def test_extract_json_raises_on_garbage():
    with pytest.raises(LLMError):
        _extract_json("no json here at all")


# --- parse_residence ----------------------------------------------------------


def test_parse_residence_extracts_zones_and_edges():
    client = FakeLLMClient(text_response="""```json
    {
      "zones": [
        {"name": "Kitchen", "kind": "dwell"},
        {"name": "Hallway", "kind": "transit"},
        {"name": "Den", "kind": "lounge"}
      ],
      "edges": [["Kitchen", "Hallway"], ["Hallway", "Den"], ["bad"]]
    }
    ```""")

    res = parse_residence(client, "kitchen opens to a hallway leading to the den")

    assert [(z.name, z.kind) for z in res.zones] == [
        ("Kitchen", "dwell"),
        ("Hallway", "transit"),
        ("Den", "dwell"),  # unknown kind normalized to dwell
    ]
    assert res.edges == [("Kitchen", "Hallway"), ("Hallway", "Den")]  # malformed edge dropped
    assert client.calls[0]["format"] == "json"
    assert "hallway leading to the den" in client.calls[0]["prompt"]


# --- parse_loss_interview -----------------------------------------------------


def test_parse_loss_interview_maps_items_and_clamps_confidence():
    client = FakeLLMClient(text_response="""
    {"items": [
      {"name": "keys", "home_zone": "Kitchen", "confidence": 0.5},
      {"name": "wallet", "home_zone": null, "confidence": 1.7},
      {"name": "", "home_zone": "Den", "confidence": 0.3}
    ]}
    """)

    items = parse_loss_interview(client, "I lose my keys and wallet", zones=["Kitchen", "Den"])

    assert [(i.name, i.home_zone, i.confidence) for i in items] == [
        ("keys", "Kitchen", 0.5),
        ("wallet", None, 1.0),  # clamped to [0,1]
    ]  # nameless item dropped
    assert "- Kitchen" in client.calls[0]["prompt"]  # known zones passed as bullets


def test_parse_loss_interview_defaults_confidence_when_missing():
    client = FakeLLMClient(text_response='{"items": [{"name": "phone", "home_zone": "Den"}]}')
    items = parse_loss_interview(client, "phone", zones=["Den"])
    assert items[0].confidence == 0.5


# --- extract_surfaces ---------------------------------------------------------


def test_extract_surfaces_dedupes_and_passes_images():
    client = FakeLLMClient(vision_response='{"surfaces": ["Counter", "counter", "Table"]}')

    surfaces = extract_surfaces(client, [b"img-bytes"], zone_name="Kitchen")

    assert surfaces == ["Counter", "Table"]  # case-insensitive dedupe, order preserved
    call = client.calls[0]
    assert call["kind"] == "vision"
    assert call["format"] == "json"
    assert call["images"] == [b"img-bytes"]
    assert "Kitchen" in call["prompt"]


# --- parse_search_query -------------------------------------------------------


def test_parse_search_query_resolves_anchor_time():
    client = FakeLLMClient(
        text_response='{"item": "keys", "anchor_text": "after dinner", "anchor_time": "2026-06-19T19:30:00"}'
    )
    now = datetime(2026, 6, 19, 21, 0)

    parsed = parse_search_query(client, "where are my keys, I had them after dinner", now=now, items=["keys"])

    assert parsed == ParsedQuery("keys", "after dinner", datetime(2026, 6, 19, 19, 30))
    assert now.isoformat(timespec="seconds") in client.calls[0]["prompt"]


def test_parse_search_query_no_anchor():
    client = FakeLLMClient(text_response='{"item": "wallet", "anchor_text": null, "anchor_time": null}')
    parsed = parse_search_query(client, "wallet?", now=datetime(2026, 6, 19, 21, 0), items=["wallet"])
    assert parsed.item == "wallet"
    assert parsed.anchor_text is None
    assert parsed.anchor_time is None


def test_parse_search_query_tolerates_trailing_z_and_bad_time():
    client = FakeLLMClient(text_response='{"item": "keys", "anchor_time": "2026-06-19T19:30:00Z"}')
    parsed = parse_search_query(client, "keys", now=datetime(2026, 6, 19, 21, 0), items=[])
    assert parsed.anchor_time is not None and parsed.anchor_time.hour == 19

    client2 = FakeLLMClient(text_response='{"item": "keys", "anchor_time": "sometime yesterday"}')
    parsed2 = parse_search_query(client2, "keys", now=datetime(2026, 6, 19, 21, 0), items=[])
    assert parsed2.anchor_time is None  # unparseable -> None, not an error


def test_parse_search_query_falls_back_to_raw_query_when_item_blank():
    client = FakeLLMClient(text_response='{"item": "", "anchor_time": null}')
    parsed = parse_search_query(client, "  my reading glasses  ", now=datetime(2026, 6, 19, 21, 0), items=[])
    assert parsed.item == "my reading glasses"


# --- phrase_reason ------------------------------------------------------------


def test_phrase_reason_returns_single_clean_line():
    client = FakeLLMClient(text_response='\n  "It might be on the couch, where you settled in after dinner."  \n')

    reason = phrase_reason(
        client, item="keys", zone="Living Room", grounding="longest dwell since the anchor", surface="couch"
    )

    assert reason == "It might be on the couch, where you settled in after dinner."
    call = client.calls[0]
    assert call["task"] == "phrase_reason"
    assert call["format"] is None  # phrasing is free text, not JSON
    assert "longest dwell since the anchor" in call["prompt"]
    assert "couch" in call["prompt"]


def test_phrase_reason_passes_none_surface_marker():
    client = FakeLLMClient(text_response="Worth a look in the den — you spent a while there.")
    reason = phrase_reason(client, item="wallet", zone="Den", grounding="recent dwell")
    assert reason.startswith("Worth a look")
    assert "none" in client.calls[0]["prompt"]  # surface marker when no surface given
