"""Stats view (M5): is the system visibly smarter on the fifth loss than the first?

Reads the append-only find history and trends the two instrumented metrics — how many
places were checked before the item turned up, and where in the ranking the actual spot
appeared. "Fewer places checked over time" is the project's headline success criterion,
so that's the metric the page leads with.

The summary math is a pure function (:func:`summarize_finds`) over find rows, kept out of
the route so it can be tested without a DB or a browser. It is *reporting*, not ranking —
the deterministic engine is untouched here. Nothing about the silent memory-trust log is
surfaced (a non-negotiable): this page only ever shows the user's own search outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import db
from .deps import get_conn, get_templates

router = APIRouter(prefix="/stats")

# A trend over noisy small samples is misleading; wait for a real history before claiming
# one. Five resolved losses is the project's exit criterion, so four is enough to split.
_MIN_FINDS_FOR_TREND = 4


@dataclass(frozen=True)
class FindStats:
    """A reporting summary of the find history — never feeds back into ranking."""

    total: int
    avg_places: float | None = None
    early_avg: float | None = None
    recent_avg: float | None = None
    trend: str = "none"  # none | early | improving | steady | slipping
    rows: list[dict] = field(default_factory=list)


def _places(find: Mapping[str, Any]) -> int | None:
    value = find["places_checked"]
    return int(value) if value is not None else None


def summarize_finds(finds: Sequence[Mapping[str, Any]]) -> FindStats:
    """Roll a chronological list of find rows into a reportable trend.

    ``finds`` is oldest-first (as :func:`db.list_finds` returns). The headline metric is
    places-checked; the trend compares the earlier half of the history against the more
    recent half, and only commits to a direction once there's enough history to mean it.
    """
    n = len(finds)
    if n == 0:
        return FindStats(total=0)

    places = [p for p in (_places(f) for f in finds) if p is not None]
    avg = sum(places) / len(places) if places else None
    scale = max(places) if places else 1

    rows = []
    for f in finds:
        pc = _places(f)
        rows.append(
            {
                "item": f["item_name"],
                "zone": f["zone_name"],
                "places_checked": pc,
                "rank": f["was_suggested_rank"],
                # Shorter bar = fewer places checked = better. 0 width when unknown.
                "bar_pct": round(100 * pc / scale) if pc and scale else 0,
            }
        )

    if len(places) >= _MIN_FINDS_FOR_TREND:
        half = len(places) // 2
        early_avg = sum(places[:half]) / half
        recent_avg = sum(places[half:]) / (len(places) - half)
        if recent_avg < early_avg:
            trend = "improving"
        elif recent_avg > early_avg:
            trend = "slipping"
        else:
            trend = "steady"
    else:
        early_avg = recent_avg = None
        trend = "early"

    return FindStats(
        total=n,
        avg_places=avg,
        early_avg=early_avg,
        recent_avg=recent_avg,
        trend=trend,
        rows=rows,
    )


@router.get("", response_class=HTMLResponse)
def stats_view(
    request: Request,
    conn=Depends(get_conn),
    templates: Jinja2Templates = Depends(get_templates),
):
    """Show the find-history trend: places checked over time and rank-of-actual."""
    stats = summarize_finds(db.list_finds(conn))
    return templates.TemplateResponse(request, "stats.html", {"stats": stats})


__all__ = ["FindStats", "router", "summarize_finds"]
