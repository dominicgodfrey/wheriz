"""Onboarding wizard routes: rooms -> photos -> loss interview.

Each step parses the user's input through the LLM edge layer into *provisional* results,
shows them for review/edit (the LLM never decides anything unilaterally), and persists
only on explicit confirm. Every step degrades gracefully when the local model is offline
so setup is never fully blocked on Ollama.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import db
from ..llm.client import LLMClient
from ..llm.tasks import (
    ParsedItem,
    ParsedResidence,
    ParsedZone,
    extract_surfaces,
    parse_loss_interview,
    parse_residence,
)
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


# --- Photos -> surfaces -------------------------------------------------------
#
# Photos are read into memory for surface extraction and never written to disk — the
# vision model's only job is to catalogue *places*, and storing personal room photos
# would widen the privacy surface for no engine benefit. The call log is sanitized
# (image bytes summarized, not stored) by the client layer.


@router.get("/photos", response_class=HTMLResponse)
def photos_form(
    request: Request,
    conn=Depends(get_conn),
    templates: Jinja2Templates = Depends(get_templates),
):
    """Step 2: per dwell room, offer a photo to extract surfaces from (all optional)."""
    zones = db.list_dwell_zones(conn)
    rows = [{"zone": z, "surfaces": db.list_surfaces(conn, z.id)} for z in zones]
    return templates.TemplateResponse(
        request, "onboarding/photos.html", {"rows": rows, "has_zones": bool(zones)}
    )


@router.post("/photos/{zone_id}", response_class=HTMLResponse)
async def photos_extract(
    zone_id: str,
    request: Request,
    photo: UploadFile = File(None),
    conn=Depends(get_conn),
    llm: LLMClient = Depends(get_llm),
    templates: Jinja2Templates = Depends(get_templates),
):
    """Extract candidate surfaces from an uploaded room photo for review (none saved)."""
    zone = db.get_zone(conn, zone_id)
    if zone is None:
        return RedirectResponse("/onboarding/photos", status_code=303)

    detected: list[str] = []
    note: str | None = None
    if photo is not None and photo.filename:
        data = await photo.read()
        if data and llm.is_available():
            detected = extract_surfaces(llm, [data], zone.name)
        elif data and not llm.is_available():
            note = "offline"

    return templates.TemplateResponse(
        request,
        "onboarding/photos_review.html",
        {"zone": zone, "detected": detected, "note": note},
    )


@router.post("/photos/{zone_id}/save")
async def photos_save(zone_id: str, request: Request, conn=Depends(get_conn)):
    """Persist the chosen surfaces (detected = photo source, typed = manual)."""
    zone = db.get_zone(conn, zone_id)
    if zone is None:
        return RedirectResponse("/onboarding/photos", status_code=303)

    existing = {s.name.lower() for s in db.list_surfaces(conn, zone_id)}
    form = await request.form()

    def _add(name: str, source: str) -> None:
        name = name.strip()
        if name and name.lower() not in existing:
            db.create_surface(conn, zone_id, name, source=source)
            existing.add(name.lower())

    for name in form.getlist("surface"):  # detected + still checked
        _add(str(name), "photo")
    for line in str(form.get("manual_text", "")).splitlines():  # typed by hand
        _add(line, "manual")

    return RedirectResponse("/onboarding/photos", status_code=303)


# --- Loss interview -> items + seeded priors ----------------------------------


def _combine_answers(q1: str, q2: str, q3: str) -> str:
    """Fold the three interview questions into one block for the parser."""
    parts = [
        f"Things often misplaced: {q1.strip()}" if q1.strip() else "",
        f"Where they usually live: {q2.strip()}" if q2.strip() else "",
        f"Where they often end up instead: {q3.strip()}" if q3.strip() else "",
    ]
    return "\n".join(p for p in parts if p)


def _offline_items(q1: str) -> list[ParsedItem]:
    """No model: split the 'often misplaced' answer into bare items, no home guess."""
    raw = q1.replace(",", "\n")
    return [ParsedItem(name=line.strip()) for line in raw.splitlines() if line.strip()]


@router.get("/items", response_class=HTMLResponse)
def items_form(
    request: Request,
    conn=Depends(get_conn),
    templates: Jinja2Templates = Depends(get_templates),
):
    """Step 3: a short interview about the things that go missing."""
    return templates.TemplateResponse(
        request, "onboarding/items.html", {"zone_count": db.count_zones(conn)}
    )


@router.post("/items/parse", response_class=HTMLResponse)
def items_parse(
    request: Request,
    q1: str = Form(""),
    q2: str = Form(""),
    q3: str = Form(""),
    conn=Depends(get_conn),
    llm: LLMClient = Depends(get_llm),
    templates: Jinja2Templates = Depends(get_templates),
):
    """Parse the answers into reviewable items + home guesses (nothing saved yet)."""
    zones = db.list_zones(conn)
    available = llm.is_available()
    if available:
        parsed = parse_loss_interview(llm, _combine_answers(q1, q2, q3), [z.name for z in zones])
    else:
        parsed = _offline_items(q1)

    rows = []
    for item in parsed:
        home = db.get_zone_by_name(conn, item.home_zone) if item.home_zone else None
        rows.append(
            {"name": item.name, "home_id": home.id if home else "", "confidence": item.confidence}
        )
    return templates.TemplateResponse(
        request,
        "onboarding/items_review.html",
        {"rows": rows, "zones": zones, "offline": not available},
    )


@router.post("/items/confirm")
async def items_confirm(request: Request, conn=Depends(get_conn)):
    """Persist confirmed items and seed each home prior from its confidence."""
    form = await request.form()
    names = form.getlist("item_name")
    homes = form.getlist("item_home")
    confidences = form.getlist("item_confidence")

    for name, home, confidence in zip(names, homes, confidences):
        name = str(name).strip()
        if not name:  # cleared = dropped
            continue
        home_id = str(home) or None
        if home_id and db.get_zone(conn, home_id) is None:  # stale/invalid selection
            home_id = None
        item_id = db.create_item(conn, name, home_zone_id=home_id)
        if home_id:
            try:
                weight = float(confidence)
            except (TypeError, ValueError):
                weight = 0.5
            db.set_prior(conn, item_id, home_id, weight)

    return RedirectResponse("/onboarding/done", status_code=303)


@router.get("/done", response_class=HTMLResponse)
def done(
    request: Request,
    conn=Depends(get_conn),
    templates: Jinja2Templates = Depends(get_templates),
):
    """Wrap-up: a warm summary of what was set up."""
    return templates.TemplateResponse(
        request,
        "onboarding/done.html",
        {"zone_count": db.count_zones(conn), "item_count": db.count_items(conn)},
    )
