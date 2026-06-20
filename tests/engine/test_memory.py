"""Tests for anchor widening and the silent claimed-vs-actual log."""

from datetime import datetime, timedelta

import pytest

from wwiw.engine.memory import (
    adjacent_zone_residual,
    entries_in_window,
    observe_claim,
    widen_anchor_window,
)
from wwiw.engine.types import AnchorWindow, DwellEntry, ScoringConfig


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 19, hour, minute)


# --- widen_anchor_window ------------------------------------------------------


def test_widen_extends_start_backward_by_fraction():
    anchor = _dt(18, 0)
    now = _dt(20, 0)  # 2h elapsed
    window = widen_anchor_window(anchor, now, ScoringConfig(time_widen_fraction=0.5))
    # 50% of 2h = 1h earlier
    assert window.start == _dt(17, 0)
    assert window.end == now


def test_widen_end_never_before_now_even_if_anchor_in_future():
    anchor = _dt(20, 0)
    now = _dt(18, 0)  # claimed future relative to query
    window = widen_anchor_window(anchor, now)
    # elapsed clamped to 0 → no widening; end is the later of the two
    assert window.start == anchor
    assert window.end == anchor


def test_widen_with_zero_fraction_is_noop_start():
    anchor = _dt(18, 0)
    now = _dt(20, 0)
    window = widen_anchor_window(anchor, now, ScoringConfig(time_widen_fraction=0.0))
    assert window.start == anchor
    assert window.end == now


# --- entries_in_window --------------------------------------------------------


def test_entries_in_window_keeps_overlapping_and_sorts():
    window = AnchorWindow(start=_dt(17, 0), end=_dt(20, 0))
    before = DwellEntry("hall", _dt(15, 0), _dt(15, 30))  # fully before -> dropped
    overlap_start = DwellEntry("couch", _dt(16, 30), _dt(17, 30))  # straddles start -> kept
    inside = DwellEntry("kitchen", _dt(18, 0), _dt(18, 40))  # inside -> kept
    after = DwellEntry("bed", _dt(21, 0), _dt(21, 30))  # fully after -> dropped
    timeline = [after, inside, overlap_start, before]

    result = entries_in_window(timeline, window)

    assert [e.zone_id for e in result] == ["couch", "kitchen"]


def test_entries_touching_boundary_are_excluded():
    window = AnchorWindow(start=_dt(17, 0), end=_dt(20, 0))
    # exit exactly at start, enter exactly at end -> no real overlap
    ends_at_start = DwellEntry("a", _dt(16, 0), _dt(17, 0))
    starts_at_end = DwellEntry("b", _dt(20, 0), _dt(21, 0))
    result = entries_in_window([ends_at_start, starts_at_end], window)
    assert result == []


# --- adjacent_zone_residual ---------------------------------------------------


def test_adjacent_residual_gives_neighbors_small_mass():
    adjacency = {"entrance": ["hall", "living"]}
    residual = adjacent_zone_residual(
        "entrance", adjacency, ScoringConfig(adjacency_residual=0.05)
    )
    assert residual == {"hall": 0.05, "living": 0.05}


def test_adjacent_residual_empty_without_claimed_zone():
    assert adjacent_zone_residual(None, {"entrance": ["hall"]}) == {}


def test_adjacent_residual_empty_for_unknown_zone():
    assert adjacent_zone_residual("garage", {"entrance": ["hall"]}) == {}


# --- observe_claim (silent log) -----------------------------------------------


def test_observe_claim_computes_delta_and_match():
    obs = observe_claim(
        actual_zone_id="couch",
        actual_time=_dt(20, 0),
        claimed_zone_id="entrance",
        claimed_time=_dt(18, 0),
    )
    assert obs.location_matched is False
    assert obs.time_delta_seconds == pytest.approx(2 * 3600)


def test_observe_claim_match_when_same_zone():
    obs = observe_claim("couch", _dt(20, 0), claimed_zone_id="couch", claimed_time=_dt(20, 0))
    assert obs.location_matched is True
    assert obs.time_delta_seconds == 0.0


def test_observe_claim_no_claim_leaves_fields_none():
    obs = observe_claim("couch", _dt(20, 0))
    assert obs.location_matched is None
    assert obs.time_delta_seconds is None
    assert obs.claimed_zone_id is None
