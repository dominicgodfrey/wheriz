"""Tests for deterministic zone ranking."""

from datetime import datetime

import pytest

from wwiw.engine.scoring import (
    first_pass_suggestion,
    rank_of_zone,
    rank_zones,
    to_suggestions,
)
from wwiw.engine.types import DwellEntry, Item, ScoringConfig


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 19, hour, minute)


def _entry(zone: str, start: tuple[int, int], end: tuple[int, int]) -> DwellEntry:
    return DwellEntry(zone, _dt(*start), _dt(*end))


WALLET = Item(id="wallet", name="wallet", home_zone_id="entrance", home_surface_id="entry_table")
ANCHOR = _dt(18, 0)
NOW = _dt(20, 0)


# --- first_pass_suggestion ----------------------------------------------------


def test_first_pass_prefers_highest_prior():
    s = first_pass_suggestion(WALLET, priors={"entrance": 0.3, "desk": 0.7})
    assert s is not None and s.zone_id == "desk" and s.rank == 1


def test_first_pass_falls_back_to_declared_home():
    s = first_pass_suggestion(WALLET, priors={})
    assert s is not None and s.zone_id == "entrance"
    assert s.surface_id == "entry_table"  # surface hint carried


def test_first_pass_none_when_no_home_known():
    s = first_pass_suggestion(Item(id="x", name="thing"), priors={})
    assert s is None


# --- rank_zones core ----------------------------------------------------------


def test_more_dwell_ranks_higher_with_equal_failure():
    timeline = [
        _entry("couch", (18, 0), (18, 40)),  # 40 min
        _entry("kitchen", (18, 40), (18, 50)),  # 10 min
    ]
    scored = rank_zones(item=WALLET, timeline=timeline, anchor_time=ANCHOR, now=NOW)
    assert [s.zone_id for s in scored] == ["couch", "kitchen"]


def test_failure_mode_can_outweigh_dwell():
    timeline = [
        _entry("kitchen", (18, 0), (19, 0)),  # 60 min, but never loses things here
        _entry("couch", (19, 0), (19, 10)),  # 10 min, but the usual culprit
    ]
    scored = rank_zones(
        item=WALLET,
        timeline=timeline,
        anchor_time=ANCHOR,
        now=NOW,
        failure_modes={"couch": 10.0},
    )
    assert scored[0].zone_id == "couch"


def test_scores_are_normalized():
    timeline = [
        _entry("couch", (18, 0), (18, 30)),
        _entry("kitchen", (18, 30), (19, 0)),
        _entry("bedroom", (19, 0), (19, 15)),
    ]
    scored = rank_zones(item=WALLET, timeline=timeline, anchor_time=ANCHOR, now=NOW)
    assert sum(s.score for s in scored) == pytest.approx(1.0)
    assert all(0.0 <= s.score <= 1.0 for s in scored)


def test_negative_space_zone_never_suggested():
    timeline = [_entry("couch", (18, 0), (18, 40))]
    scored = rank_zones(item=WALLET, timeline=timeline, anchor_time=ANCHOR, now=NOW)
    # "garage" was never entered since the anchor and isn't adjacent to anything claimed
    assert rank_of_zone(scored, "garage") is None


def test_excluded_zone_removed():
    timeline = [
        _entry("couch", (18, 0), (18, 40)),
        _entry("kitchen", (18, 40), (19, 0)),
    ]
    scored = rank_zones(
        item=WALLET,
        timeline=timeline,
        anchor_time=ANCHOR,
        now=NOW,
        excluded_zone_ids={"couch"},
    )
    assert [s.zone_id for s in scored] == ["kitchen"]


def test_plausible_set_intersected():
    timeline = [
        _entry("couch", (18, 0), (18, 40)),
        _entry("bathroom", (18, 40), (19, 0)),
    ]
    scored = rank_zones(
        item=WALLET,
        timeline=timeline,
        anchor_time=ANCHOR,
        now=NOW,
        plausible_zone_ids={"couch"},  # a wallet won't plausibly be in the bathroom
    )
    assert [s.zone_id for s in scored] == ["couch"]


def test_adjacent_zone_gets_small_residual_without_dwell():
    timeline = [_entry("couch", (18, 0), (18, 40))]
    scored = rank_zones(
        item=WALLET,
        timeline=timeline,
        anchor_time=ANCHOR,
        now=NOW,
        claimed_zone_id="entrance",
        adjacency={"entrance": ["hall"]},
    )
    by_zone = {s.zone_id: s for s in scored}
    assert "hall" in by_zone  # widened in despite zero dwell
    assert by_zone["hall"].dwell_seconds == 0.0
    assert by_zone["hall"].score < by_zone["couch"].score


def test_caps_at_max_candidates_and_renormalizes():
    timeline = [
        _entry("couch", (18, 0), (18, 30)),
        _entry("kitchen", (18, 30), (19, 0)),
        _entry("bedroom", (19, 0), (19, 20)),
    ]
    scored = rank_zones(
        item=WALLET,
        timeline=timeline,
        anchor_time=ANCHOR,
        now=NOW,
        config=ScoringConfig(max_candidates=2),
    )
    assert len(scored) == 2
    assert sum(s.score for s in scored) == pytest.approx(1.0)


def test_empty_timeline_returns_no_candidates():
    scored = rank_zones(item=WALLET, timeline=[], anchor_time=ANCHOR, now=NOW)
    assert scored == []


# --- to_suggestions / rank_of_zone -------------------------------------------


def test_to_suggestions_assigns_ranks_and_surface_hint():
    timeline = [
        _entry("couch", (18, 0), (18, 40)),
        _entry("kitchen", (18, 40), (19, 0)),
    ]
    scored = rank_zones(item=WALLET, timeline=timeline, anchor_time=ANCHOR, now=NOW)
    suggestions = to_suggestions(scored, surface_by_zone={"couch": "cushions"})
    assert [s.rank for s in suggestions] == [1, 2]
    assert suggestions[0].surface_id == "cushions"
    assert suggestions[1].surface_id is None


def test_rank_of_zone_is_one_based():
    timeline = [
        _entry("couch", (18, 0), (18, 40)),
        _entry("kitchen", (18, 40), (19, 0)),
    ]
    scored = rank_zones(item=WALLET, timeline=timeline, anchor_time=ANCHOR, now=NOW)
    assert rank_of_zone(scored, "couch") == 1
    assert rank_of_zone(scored, "kitchen") == 2
    assert rank_of_zone(scored, "nowhere") is None
