"""Keystone integration test: the Stage 0 exit criterion as a unit test.

A scripted sequence of five synthetic loss events. The item's declared home is the
entrance; the user checks there first, rejects it, and the item actually turns up on
the couch every time. The couch starts buried (low dwell) behind higher-dwell zones,
so it ranks poorly at first. As the failure-mode memory learns, the couch's rank in
the rejection pass must demonstrably improve — visibly smarter on the fifth loss than
the first. This is the canonical "not at entrance → reasons to couch → confirm →
suggests couch faster next time" loop, run end-to-end through the pure engine.
"""

from datetime import datetime

from wwiw.engine.learning import apply_find
from wwiw.engine.scoring import first_pass_suggestion, rank_of_zone, rank_zones
from wwiw.engine.types import DwellEntry, Item


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 19, hour, minute)


WALLET = Item(id="wallet", name="wallet", home_zone_id="entrance", home_surface_id="entry_table")
ANCHOR = _dt(18, 0)
NOW = _dt(20, 0)


def _recurring_timeline() -> list[DwellEntry]:
    """The user's habitual evening: lots of kitchen, some office, a little couch.

    The couch — where the wallet actually ends up — has the *least* dwell, so dwell
    alone ranks it last. Only failure-mode learning can lift it.
    """
    return [
        DwellEntry("entrance", _dt(17, 55), _dt(18, 0)),  # passed through; checked & rejected
        DwellEntry("kitchen", _dt(18, 0), _dt(19, 0)),  # 60 min
        DwellEntry("office", _dt(19, 0), _dt(19, 30)),  # 30 min
        DwellEntry("couch", _dt(19, 30), _dt(19, 45)),  # 15 min — the real culprit
    ]


def test_rank_of_actual_improves_over_five_losses():
    priors: dict[str, float] = {"entrance": 1.0}
    failure_modes: dict[str, float] = {}
    ranks: list[int] = []

    for _ in range(5):
        timeline = _recurring_timeline()

        # First pass honors the home spot; the user checks there and rejects it.
        first = first_pass_suggestion(WALLET, priors={"entrance": 1.0})
        assert first is not None and first.zone_id == "entrance"

        # Rejection pass: rank everywhere dwelled since the anchor, home excluded.
        scored = rank_zones(
            item=WALLET,
            timeline=timeline,
            anchor_time=ANCHOR,
            now=NOW,
            failure_modes=failure_modes,
            excluded_zone_ids={"entrance"},
        )

        # Invariants hold every round.
        assert sum(s.score for s in scored) == 1.0 or abs(sum(s.score for s in scored) - 1.0) < 1e-9
        assert rank_of_zone(scored, "garage") is None  # never-entered zone stays pruned

        rank = rank_of_zone(scored, "couch")
        assert rank is not None
        ranks.append(rank)

        # Confirmed find on the couch — away from home, so failure memory learns.
        priors, failure_modes = apply_find(
            priors, failure_modes, found_zone_id="couch", home_zone_id="entrance"
        )

    # Visibly smarter on the fifth loss than the first.
    assert ranks[-1] < ranks[0], f"expected improvement, got {ranks}"
    assert ranks[-1] == 1, f"couch should be top suggestion by the end, got {ranks}"
    # Monotonic: learning never makes the actual location rank worse.
    assert all(later <= earlier for earlier, later in zip(ranks, ranks[1:])), ranks


def test_couch_is_top_suggestion_only_after_learning():
    """The very first loss should not already rank the couch first (cold start)."""
    scored = rank_zones(
        item=WALLET,
        timeline=_recurring_timeline(),
        anchor_time=ANCHOR,
        now=NOW,
        failure_modes={},
        excluded_zone_ids={"entrance"},
    )
    assert rank_of_zone(scored, "couch") != 1
