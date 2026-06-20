"""Tests for find-driven learning updates."""

import pytest

from wwiw.engine.learning import apply_find, update_failure_mode, update_home_prior
from wwiw.engine.types import LearningConfig


# --- update_home_prior --------------------------------------------------------


def test_home_prior_shifts_mass_toward_found_zone():
    priors = {"entrance": 1.0}
    updated = update_home_prior(priors, "couch", LearningConfig(prior_decay=0.7))
    assert updated["entrance"] == pytest.approx(0.7)
    assert updated["couch"] == pytest.approx(0.3)


def test_home_prior_stays_normalized():
    priors = {"entrance": 0.6, "couch": 0.4}
    updated = update_home_prior(priors, "couch")
    assert sum(updated.values()) == pytest.approx(1.0)


def test_home_prior_from_empty_seeds_found_zone():
    updated = update_home_prior({}, "couch", LearningConfig(prior_decay=0.7))
    assert updated == {"couch": pytest.approx(0.3)}


def test_repeated_finds_increase_found_zone_share():
    priors = {"entrance": 1.0}
    share = []
    for _ in range(5):
        priors = update_home_prior(priors, "couch")
        share.append(priors["couch"])
    assert share == sorted(share)  # monotonically non-decreasing
    assert share[-1] > share[0]


def test_home_prior_does_not_mutate_input():
    priors = {"entrance": 1.0}
    update_home_prior(priors, "couch")
    assert priors == {"entrance": 1.0}


# --- update_failure_mode ------------------------------------------------------


def test_away_find_increments_found_zone():
    fm = update_failure_mode({}, "couch", home_zone_id="entrance")
    assert fm["couch"] == pytest.approx(1.0)


def test_away_find_decays_other_zones():
    fm = {"desk": 1.0}
    updated = update_failure_mode(
        fm, "couch", home_zone_id="entrance", config=LearningConfig(failure_decay=0.9)
    )
    assert updated["desk"] == pytest.approx(0.9)
    assert updated["couch"] == pytest.approx(1.0)


def test_home_find_leaves_failure_memory_unchanged():
    fm = {"couch": 3.0}
    updated = update_failure_mode(fm, "entrance", home_zone_id="entrance")
    assert updated == {"couch": 3.0}
    assert updated is not fm  # returns a copy, not the same object


def test_repeated_away_finds_grow_monotonically():
    fm: dict[str, float] = {}
    weights = []
    for _ in range(5):
        fm = update_failure_mode(fm, "couch", home_zone_id="entrance")
        weights.append(fm["couch"])
    assert weights == sorted(weights)
    assert weights[-1] > weights[0]


def test_failure_mode_does_not_mutate_input():
    fm = {"desk": 1.0}
    update_failure_mode(fm, "couch", home_zone_id="entrance")
    assert fm == {"desk": 1.0}


# --- apply_find ---------------------------------------------------------------


def test_apply_find_updates_both_maps():
    priors, fm = apply_find(
        priors={"entrance": 1.0},
        failure_modes={},
        found_zone_id="couch",
        home_zone_id="entrance",
    )
    assert priors["couch"] > 0.0
    assert fm["couch"] > 0.0


def test_apply_find_at_home_only_touches_prior():
    priors, fm = apply_find(
        priors={"entrance": 1.0},
        failure_modes={"couch": 2.0},
        found_zone_id="entrance",
        home_zone_id="entrance",
    )
    assert priors["entrance"] > 0.0
    assert fm == {"couch": 2.0}  # failure memory untouched at a home find
