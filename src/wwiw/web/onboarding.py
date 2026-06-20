"""Onboarding wizard routes: rooms -> photos -> loss interview.

Each step parses the user's input through the LLM edge layer into *provisional* results,
shows them for review/edit (the LLM never decides anything unilaterally), and persists
only on explicit confirm. Every step degrades gracefully when the local model is offline
so setup is never fully blocked on Ollama.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import db
from ..llm.client import LLMClient
from ..llm.tasks import ParsedResidence, ParsedZone, parse_residence
from .deps import get_conn, get_llm, get_templates

router = APIRouter(prefix="/onboarding")

_STEP = "rooms"


def _offline_rooms(description: str) -> ParsedResidence:
    """No model: treat each non-empty line as a dwell room, no inferred connections."""
    zones = [ParsedZone(name=line.strip()) for line in description.splitlines() if line.strip()]
    return ParsedResidence(zones=zones, edges=[])


# --- Rooms --------------------------------------------------------------------


@router.get("/rooms", response_class=HTMLResponse)
def rooms_form(
    request: Request,
    conn=Depends(get_conn),
    templates: Jinja2Templates = Depends(get_templates),
):
    """Step 1: ask for a natural-language description of the home."""
    return templates.TemplateResponse(
        request, "onboarding/rooms.html", {"zone_count": db.count_zones(conn)}
    )


@router.post("/rooms/parse", response_class=HTMLResponse)
def rooms_parse(
    request: Request,
    description: str = Form(""),
    llm: LLMClient = Depends(get_llm),
    templates: Jinja2Templates = Depends(get_templates),
):
    """Parse the description into a reviewable room graph (nothing saved yet)."""
    available = llm.is_available()
    residence = parse_residence(llm, description) if available else _offline_rooms(description)
    return templates.TemplateResponse(
        request,
        "onboarding/rooms_review.html",
        {"zones": residence.zones, "edges": residence.edges, "offline": not available},
    )


@router.post("/rooms/confirm")
async def rooms_confirm(request: Request, conn=Depends(get_conn)):
    """Persist the confirmed rooms + connections, then move on to photos."""
    form = await request.form()
    names = form.getlist("zone_name")
    kinds = form.getlist("zone_kind")

    name_to_id: dict[str, str] = {}
    for name, kind in zip(names, kinds):
        name = str(name).strip()
        if not name:  # cleared field = user dropped this room
            continue
        zone_id = db.create_zone(conn, name, str(kind))
        name_to_id[name.lower()] = zone_id

    for edge in form.getlist("edge"):
        a, _, b = str(edge).partition("||")
        a_id, b_id = name_to_id.get(a.strip().lower()), name_to_id.get(b.strip().lower())
        if a_id and b_id:
            db.add_edge(conn, a_id, b_id)

    return RedirectResponse("/onboarding/photos", status_code=303)
