"""Tests for engine domain dataclasses."""

from datetime import datetime, timedelta

import pytest

from wwiw.engine.types import (
    AnchorWindow,
    DwellEntry,
    Item,
    LearningConfig,
    MemoryObservation,
    ScoredZone,
    ScoringConfig,
    Suggestion,
    Surface,
    Zone,
)


def test_zone_defaults_to_dwell_kind():
    z = Zone(id="z1", name="Kitchen")
    assert z.kind == "dwell"


def test_surface_defaults_to_manual_source():
    s = Surface(id="s1", zone_id="z1", name="counter")
    assert s.source == "manual"


def test_item_optional_home_fields():
    i = Item(id="i1", name="wallet")
    assert i.home_zone_id is None
    assert i.home_surface_id is None


def test_dwell_seconds_derived_from_enter_exit():
    enter = datetime(2026, 6, 19, 18, 0, 0)
    entry = DwellEntry(zone_id="z1", enter=enter, exit=enter + timedelta(minutes=40))
    assert entry.dwell_seconds == pytest.approx(40 * 60)


def test_dwell_seconds_clamped_for_inverted_interval():
    t = datetime(2026, 6, 19, 18, 0, 0)
    entry = DwellEntry(zone_id="z1", enter=t, exit=t - timedelta(minutes=5))
    assert entry.dwell_seconds == 0.0


def test_frozen_dataclasses_are_immutable():
    z = Zone(id="z1", name="Kitchen")
    with pytest.raises(Exception):
        z.name = "Bedroom"  # type: ignore[misc]


def test_config_defaults_are_sane():
    sc = ScoringConfig()
    assert 0.0 < sc.time_widen_fraction <= 1.0
    assert sc.min_candidates <= sc.max_candidates
    assert sc.failure_smoothing > 0

    lc = LearningConfig()
    assert 0.0 < lc.prior_decay < 1.0
    assert 0.0 < lc.failure_decay < 1.0
    assert lc.failure_increment > 0


def test_output_types_construct():
    anchor = AnchorWindow(start=datetime(2026, 6, 19, 18), end=datetime(2026, 6, 19, 20))
    assert anchor.end > anchor.start

    sz = ScoredZone(zone_id="z1", score=0.6, dwell_seconds=2400, failure_weight=3.0)
    assert sz.score == 0.6

    sug = Suggestion(rank=1, zone_id="z1", score=0.6)
    assert sug.surface_id is None and sug.reason is None

    obs = MemoryObservation(actual_zone_id="z2", actual_time=datetime(2026, 6, 19, 19))
    assert obs.location_matched is None
