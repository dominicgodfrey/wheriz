"""Memory-error handling: anchor widening and the silent claimed-vs-actual log.

Pure functions only. Two responsibilities:

1. **Anchor widening.** The user's stated "last contact" time is honored, then widened
   by fixed defaults to allow for ordinary memory compression ("after dinner" might have
   been earlier than they think). We also grant a small residual of plausibility to zones
   adjacent to the claimed zone. Widening is *normal brain behavior*, never distrust.

2. **Claimed-vs-actual observation.** When a find is confirmed we record what the user
   claimed against what actually happened. This is logged silently and must never be
   surfaced to the user as a score or report.

No learned fitting in the MVP — the widening fractions are fixed in ``ScoringConfig``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta

from .types import AnchorWindow, DwellEntry, MemoryObservation, ScoringConfig


def widen_anchor_window(
    anchor_time: datetime,
    now: datetime,
    config: ScoringConfig | None = None,
) -> AnchorWindow:
    """Return the evidence window for a search, widened backward in time.

    The window runs from the (widened) anchor up to ``now``. We extend the start
    earlier by ``time_widen_fraction`` of the elapsed time — the user may have last
    truly had the item before they remember. The end is never earlier than ``now``.
    """
    config = config or ScoringConfig()
    end = max(now, anchor_time)
    elapsed_seconds = max(0.0, (now - anchor_time).total_seconds())
    widen = timedelta(seconds=elapsed_seconds * config.time_widen_fraction)
    start = anchor_time - widen
    return AnchorWindow(start=start, end=end)


def entries_in_window(
    timeline: Iterable[DwellEntry],
    window: AnchorWindow,
) -> list[DwellEntry]:
    """Return timeline entries that overlap the window, in chronological order.

    An entry overlaps if any part of its interval falls inside the window. Zones
    with no overlapping entry are *negative space* and simply never appear here —
    which is how negative-space pruning happens for free downstream.
    """
    overlapping = [
        e for e in timeline if e.exit > window.start and e.enter < window.end
    ]
    return sorted(overlapping, key=lambda e: e.enter)


def adjacent_zone_residual(
    claimed_zone_id: str | None,
    adjacency: Mapping[str, Iterable[str]],
    config: ScoringConfig | None = None,
) -> dict[str, float]:
    """Return a small residual mass for each zone adjacent to the claimed zone.

    This widens plausibility one doorway out from where the user thinks they were,
    without asserting the item is there. Empty when no claimed zone is given.
    """
    if claimed_zone_id is None:
        return {}
    config = config or ScoringConfig()
    neighbors = adjacency.get(claimed_zone_id, ())
    return {zone_id: config.adjacency_residual for zone_id in neighbors}


def observe_claim(
    actual_zone_id: str,
    actual_time: datetime,
    claimed_zone_id: str | None = None,
    claimed_time: datetime | None = None,
) -> MemoryObservation:
    """Build a silent claimed-vs-actual record for the memory-trust log.

    Computes the time delta and a location-match flag when a claim was made. The
    caller persists this; it is never shown to the user.
    """
    time_delta_seconds = (
        (actual_time - claimed_time).total_seconds()
        if claimed_time is not None
        else None
    )
    location_matched = (
        claimed_zone_id == actual_zone_id if claimed_zone_id is not None else None
    )
    return MemoryObservation(
        actual_zone_id=actual_zone_id,
        actual_time=actual_time,
        claimed_zone_id=claimed_zone_id,
        claimed_time=claimed_time,
        time_delta_seconds=time_delta_seconds,
        location_matched=location_matched,
    )


__all__ = [
    "widen_anchor_window",
    "entries_in_window",
    "adjacent_zone_residual",
    "observe_claim",
]
