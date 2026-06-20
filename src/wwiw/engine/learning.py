"""Find-driven learning. Pure functions — the model gets smarter with every confirmed find.

Two updates fire on a confirmed find:

* **Home prior** (``update_home_prior``): a decaying average over zones. Each find decays
  the existing mass and adds fresh mass to where the item turned up, so recent finds weigh
  more. Self-normalizing toward a total of 1.0.
* **Failure-mode memory** (``update_failure_mode``): P(zone | not at home spot). Only an
  away-from-home find writes here — it decays the existing failure memory and adds mass to
  the zone where the item was actually found. A home-spot find leaves failure memory alone.

These return new dicts; inputs are never mutated. Counts and persistence live in the DB
layer — here we only move the decayed weights the engine ranks on.
"""

from __future__ import annotations

from collections.abc import Mapping

from .types import LearningConfig


def update_home_prior(
    priors: Mapping[str, float],
    found_zone_id: str,
    config: LearningConfig | None = None,
) -> dict[str, float]:
    """Return updated home-location priors after a find (decaying average).

    All existing weight decays by ``prior_decay``; the found zone additionally gains
    ``1 - prior_decay``. A prior distribution that sums to 1.0 stays summed to 1.0.
    """
    config = config or LearningConfig()
    decay = config.prior_decay
    updated = {zone_id: weight * decay for zone_id, weight in priors.items()}
    updated[found_zone_id] = updated.get(found_zone_id, 0.0) + (1.0 - decay)
    return updated


def update_failure_mode(
    failure_modes: Mapping[str, float],
    found_zone_id: str,
    home_zone_id: str | None,
    config: LearningConfig | None = None,
) -> dict[str, float]:
    """Return updated failure-mode weights after a find.

    Only records when the item was found *away* from its home spot: existing failure
    memory decays by ``failure_decay`` and the found zone gains ``failure_increment``.
    A find at the home spot returns the failure memory unchanged.
    """
    config = config or LearningConfig()
    if home_zone_id is not None and found_zone_id == home_zone_id:
        return dict(failure_modes)
    updated = {
        zone_id: weight * config.failure_decay for zone_id, weight in failure_modes.items()
    }
    updated[found_zone_id] = updated.get(found_zone_id, 0.0) + config.failure_increment
    return updated


def apply_find(
    priors: Mapping[str, float],
    failure_modes: Mapping[str, float],
    found_zone_id: str,
    home_zone_id: str | None,
    config: LearningConfig | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """Apply both updates for one confirmed find; return ``(priors, failure_modes)``."""
    config = config or LearningConfig()
    new_priors = update_home_prior(priors, found_zone_id, config)
    new_failure_modes = update_failure_mode(failure_modes, found_zone_id, home_zone_id, config)
    return new_priors, new_failure_modes


__all__ = ["update_home_prior", "update_failure_mode", "apply_find"]
