"""Domain dataclasses for the deterministic engine.

Pure data only — no I/O, no DB, no LLM. These types are the vocabulary the pure
scoring/learning/memory functions speak in. The DB layer (``wwiw.db``) maps rows to
and from these; the engine itself never touches a database.

The occupancy timeline is consumed as ``DwellEntry`` records of ``(zone, enter, exit)``
with ``dwell`` derived. The engine must not know whether those came from retrospective
reconstruction, a quick dwell-log, or (later) real sensors — that interface is sacred.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# --- Spatial / item vocabulary -------------------------------------------------


@dataclass(frozen=True)
class Zone:
    """A place in the residence. ``kind`` is ``"dwell"`` or ``"transit"``."""

    id: str
    name: str
    kind: str = "dwell"


@dataclass(frozen=True)
class Surface:
    """A surface within a zone (counter, couch, entry table). Decorates, never scores."""

    id: str
    zone_id: str
    name: str
    source: str = "manual"  # "photo" | "manual"


@dataclass(frozen=True)
class Item:
    """A trackable item with an optional learned home zone/surface."""

    id: str
    name: str
    home_zone_id: str | None = None
    home_surface_id: str | None = None


# --- Timeline (the sacred interface) ------------------------------------------


@dataclass(frozen=True)
class DwellEntry:
    """One occupancy interval in a zone. ``dwell`` is derived from enter/exit.

    Source-agnostic by design: retrospective, quick-log, and future sensors all
    produce the same shape.
    """

    zone_id: str
    enter: datetime
    exit: datetime

    @property
    def dwell_seconds(self) -> float:
        """Dwell duration in seconds, clamped at zero for inverted intervals."""
        return max(0.0, (self.exit - self.enter).total_seconds())


@dataclass(frozen=True)
class AnchorWindow:
    """A time window for evidence collection, after memory-error widening."""

    start: datetime
    end: datetime


# --- Engine output -------------------------------------------------------------


@dataclass(frozen=True)
class ScoredZone:
    """A candidate zone with its normalized probability and the factors behind it.

    ``score`` is normalized across the returned candidates (sums to 1.0).
    ``dwell_seconds`` and ``failure_weight`` are the raw factors, kept so the
    reason layer can ground its phrasing in real numbers.
    """

    zone_id: str
    score: float
    dwell_seconds: float
    failure_weight: float


@dataclass(frozen=True)
class Suggestion:
    """A ranked, presentable candidate at the engine/UI boundary.

    The engine fills ``rank``/``zone_id``/``score``; the surface hint and the
    LLM-phrased ``reason`` are decorated on top before display.
    """

    rank: int
    zone_id: str
    score: float
    surface_id: str | None = None
    reason: str | None = None


# --- Silent memory-trust observation ------------------------------------------


@dataclass(frozen=True)
class MemoryObservation:
    """A claimed-vs-actual record. Logged silently, never surfaced as a score."""

    actual_zone_id: str
    actual_time: datetime
    claimed_zone_id: str | None = None
    claimed_time: datetime | None = None
    time_delta_seconds: float | None = None
    location_matched: bool | None = None


# --- Tunable constants ---------------------------------------------------------


@dataclass(frozen=True)
class ScoringConfig:
    """Fixed defaults for the ranking pass. No learned fitting in the MVP."""

    time_widen_fraction: float = 0.5  # widen anchor window by ±50% of elapsed time
    adjacency_residual: float = 0.05  # small mass to zones adjacent to claimed zone
    failure_smoothing: float = 1.0  # Laplace alpha so cold-start zones aren't zeroed
    min_candidates: int = 2
    max_candidates: int = 4


@dataclass(frozen=True)
class LearningConfig:
    """Fixed defaults for find-driven updates. Recent finds weigh more."""

    prior_decay: float = 0.7  # home-prior decaying average: keep this much of the past
    failure_decay: float = 0.9  # failure-mode memory decay per find
    failure_increment: float = 1.0  # mass added to a zone on an away-from-home find


__all__ = [
    "Zone",
    "Surface",
    "Item",
    "DwellEntry",
    "AnchorWindow",
    "ScoredZone",
    "Suggestion",
    "MemoryObservation",
    "ScoringConfig",
    "LearningConfig",
]
