"""Deterministic zone ranking. Pure functions — the LLM never ranks.

Two passes, matching the find interaction:

* **First pass** (``first_pass_suggestion``): honor the item's home prior. "It usually
  lives at the entrance — checked there?" This is the single highest-prior zone.
* **Rejection pass** (``rank_zones``): the user said it's not at the home spot, so
  redistribute probability across zones that were actually dwelled in since the
  (widened) anchor, weighted by ``failure_mode_weight × dwell_weight``, intersected
  with the plausible set, with negative-space zones excluded and a small residual
  granted to zones adjacent to the claimed zone.

All scores returned are normalized across the returned candidates (sum to 1.0).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from datetime import datetime

from .memory import adjacent_zone_residual, entries_in_window, widen_anchor_window
from .types import DwellEntry, Item, ScoredZone, ScoringConfig, Suggestion


def first_pass_suggestion(
    item: Item,
    priors: Mapping[str, float] | None = None,
) -> Suggestion | None:
    """Return the home-prior zone to check first, or ``None`` if unknown.

    Prefers the highest-weight learned prior; falls back to the item's declared
    home zone. Carries the home surface as a decorating hint.
    """
    priors = priors or {}
    home_zone_id: str | None = None
    if priors:
        # Highest weight wins; ties broken by zone id for determinism.
        home_zone_id = max(sorted(priors), key=lambda z: priors[z])
    if home_zone_id is None:
        home_zone_id = item.home_zone_id
    if home_zone_id is None:
        return None
    return Suggestion(
        rank=1,
        zone_id=home_zone_id,
        score=1.0,
        surface_id=item.home_surface_id,
    )


def rank_zones(
    *,
    item: Item,
    timeline: Iterable[DwellEntry],
    anchor_time: datetime,
    now: datetime,
    failure_modes: Mapping[str, float] | None = None,
    plausible_zone_ids: Collection[str] | None = None,
    excluded_zone_ids: Collection[str] | None = None,
    claimed_zone_id: str | None = None,
    adjacency: Mapping[str, Iterable[str]] | None = None,
    config: ScoringConfig | None = None,
) -> list[ScoredZone]:
    """Rank candidate zones for the rejection pass.

    ``score(zone) ∝ (failure_weight + smoothing) × normalized_dwell + adjacency_residual``

    Candidates are zones dwelled in within the widened anchor window (negative-space
    zones never enter the set), optionally intersected with ``plausible_zone_ids`` and
    with ``excluded_zone_ids`` removed. Zones adjacent to ``claimed_zone_id`` receive a
    small residual even with no dwell. Returns up to ``config.max_candidates`` zones,
    scores normalized to sum to 1.0, ordered most-likely first.
    """
    config = config or ScoringConfig()
    failure_modes = failure_modes or {}
    excluded = set(excluded_zone_ids or ())
    plausible = set(plausible_zone_ids) if plausible_zone_ids is not None else None

    # 1. Collect dwell evidence within the widened window.
    window = widen_anchor_window(anchor_time, now, config)
    entries = entries_in_window(timeline, window)
    dwell_by_zone: dict[str, float] = {}
    for entry in entries:
        dwell_by_zone[entry.zone_id] = (
            dwell_by_zone.get(entry.zone_id, 0.0) + entry.dwell_seconds
        )

    # 2. Adjacency widening: a small residual one doorway out from the claim.
    residual_by_zone = adjacent_zone_residual(claimed_zone_id, adjacency or {}, config)

    # 3. Candidate set = dwelled zones ∪ adjacency-residual zones, minus excluded,
    #    intersected with the plausible set when one is given.
    candidate_ids = set(dwell_by_zone) | set(residual_by_zone)
    candidate_ids -= excluded
    if plausible is not None:
        candidate_ids &= plausible
    if not candidate_ids:
        return []

    # 4. Normalize dwell into a weight in [0, 1] so it composes with failure mass.
    total_dwell = sum(dwell_by_zone.get(z, 0.0) for z in candidate_ids)

    raw: dict[str, float] = {}
    for zone_id in candidate_ids:
        dwell_seconds = dwell_by_zone.get(zone_id, 0.0)
        dwell_weight = dwell_seconds / total_dwell if total_dwell > 0 else 0.0
        failure_weight = failure_modes.get(zone_id, 0.0) + config.failure_smoothing
        raw[zone_id] = failure_weight * dwell_weight + residual_by_zone.get(zone_id, 0.0)

    # 5. Order by raw score; tie-break by dwell then zone id for determinism.
    ordered = sorted(
        candidate_ids,
        key=lambda z: (raw[z], dwell_by_zone.get(z, 0.0), z),
        reverse=True,
    )
    top = ordered[: config.max_candidates]

    # 6. Normalize the kept candidates to sum to 1.0.
    kept_total = sum(raw[z] for z in top)
    return [
        ScoredZone(
            zone_id=z,
            score=(raw[z] / kept_total) if kept_total > 0 else (1.0 / len(top)),
            dwell_seconds=dwell_by_zone.get(z, 0.0),
            failure_weight=failure_modes.get(z, 0.0),
        )
        for z in top
    ]


def to_suggestions(
    scored: Iterable[ScoredZone],
    surface_by_zone: Mapping[str, str] | None = None,
) -> list[Suggestion]:
    """Assemble ranked, presentable suggestions from scored zones.

    Ranks are assigned by order. A surface hint decorates a suggestion when one is
    known for its zone — surfaces decorate, they never change the ranking.
    """
    surface_by_zone = surface_by_zone or {}
    return [
        Suggestion(
            rank=i,
            zone_id=sz.zone_id,
            score=sz.score,
            surface_id=surface_by_zone.get(sz.zone_id),
        )
        for i, sz in enumerate(scored, start=1)
    ]


def rank_of_zone(scored: Iterable[ScoredZone], zone_id: str) -> int | None:
    """Return the 1-based rank of ``zone_id`` among scored zones, or ``None``.

    This is the core metric (rank-of-actual-location) the stats view trends.
    """
    for i, sz in enumerate(scored, start=1):
        if sz.zone_id == zone_id:
            return i
    return None


__all__ = [
    "first_pass_suggestion",
    "rank_zones",
    "to_suggestions",
    "rank_of_zone",
]
