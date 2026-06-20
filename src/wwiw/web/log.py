"""Quick dwell-log: the second, optional half of the hybrid timeline stub.

The find loop reconstructs a timeline retrospectively at query time. This page lets the
user *proactively* drop an occupancy interval into the same timeline — "I was in the
kitchen for a while" — so that when something later goes missing, the dwell evidence is
already there. It writes ``(zone, enter, exit, source=quicklog)`` rows through the one
timeline-write boundary (:func:`db.add_dwell_entry`); the engine reads them back exactly
like any other dwell and never learns they were hand-logged. The interface is sacred.

Absolute clock barely matters to ranking (it turns on *which* zones and relative dwell),
so a logged stay is modelled as ending "now" and reaching back by a coarse duration.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import db
from .deps import get_conn, get_templates

router = APIRouter(prefix="/log")

# Coarse "how long were you there" choices -> a span reaching back from now. These mirror
# the retrace interview's vocabulary so the two halves of the stub feel like one feature.
_DURATIONS: dict[str, tuple[str, timedelta]] = {
    "brief": ("a few minutes", timedelta(minutes=15)),
    "while": ("a little while", timedelta(hours=1)),
    "long": ("a good while", timedelta(hours=3)),
}
_DEFAULT_DURATION = "while"


def _render(
    request: Request,
    templates: Jinja2Templates,
    conn,
    *,
    logged: dict | None = None,
    error: str | None = None,
):
    """Render the log page with the room picker and a recent-entries echo."""
    return templates.TemplateResponse(
        request,
        "log.html",
        {
            "zones": db.list_dwell_zones(conn),
            "durations": [(key, label) for key, (label, _) in _DURATIONS.items()],
            "default_duration": _DEFAULT_DURATION,
            "recent": db.recent_dwell_entries(conn, limit=8),
            "logged": logged,
            "error": error,
        },
    )


@router.get("", response_class=HTMLResponse)
def log_form(
    request: Request,
    conn=Depends(get_conn),
    templates: Jinja2Templates = Depends(get_templates),
):
    """Show the quick dwell-log entry form plus what's recently been logged."""
    return _render(request, templates, conn)


@router.post("", response_class=HTMLResponse)
def log_submit(
    request: Request,
    zone_id: str = Form(""),
    dwell: str = Form(_DEFAULT_DURATION),
    conn=Depends(get_conn),
    templates: Jinja2Templates = Depends(get_templates),
):
    """Append a quick-logged occupancy interval ending now, then re-show the form.

    A missing/unknown room re-asks rather than writing junk into the timeline.
    """
    zone = db.get_zone(conn, zone_id) if zone_id else None
    if zone is None:
        return _render(request, templates, conn, error="Pick a room to log.")

    label, span = _DURATIONS.get(dwell, _DURATIONS[_DEFAULT_DURATION])
    now = datetime.now()
    db.add_dwell_entry(conn, zone.id, now - span, now, source="quicklog")
    return _render(
        request, templates, conn, logged={"zone": zone.name, "duration": label}
    )


__all__ = ["router"]
