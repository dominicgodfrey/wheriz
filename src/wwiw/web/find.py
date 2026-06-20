"""The find loop: query -> first-pass -> retrospective interview -> ranked suggestions
-> confirm -> learning. This module owns the highest-leverage UX in the app.

The deterministic engine decides everything that matters here; this layer only collects
the user's language (parsed at the edge), reconstructs the occupancy timeline from their
answers, and presents engine output as *suggestions* the user taps. Every place the user
rules out and every confirmed find is persisted append-only, and a confirmed find feeds
the learning update so the next search ranks better.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import db
from ..engine.scoring import first_pass_suggestion, rank_zones
from ..engine.types import DwellEntry, ScoredZone
from ..llm.client import LLMClient, LLMError
from ..llm.tasks import ParsedQuery, parse_search_query, phrase_reason
from .deps import get_conn, get_llm, get_templates

router = APIRouter(prefix="/find")

# When the user gives no usable memory anchor, retrace this far back by default. The exact
# span barely matters — ranking turns on *which* zones and their relative dwell, not the
# absolute clock — but a finite window keeps the synthesized timeline sane.
_DEFAULT_LOOKBACK = timedelta(hours=6)

# Coarse dwell choices in the retrace interview -> relative weights for time-splitting.
_DWELL_WEIGHTS = {"brief": 1.0, "while": 2.0, "long": 3.0}


# --- query parsing (edge layer, with offline fallback) ------------------------


def _parse_query(
    llm: LLMClient, query: str, *, now: datetime, item_names: list[str]
) -> ParsedQuery:
    """Parse the query through the model, degrading to the raw text when it's offline.

    Mirrors onboarding: ``is_available()`` only means the server answered, so we still
    catch ``LLMError`` and fall back. Offline, the whole query stands in as the item name
    (substring matching against known items usually still resolves it) with no anchor.
    """
    if llm.is_available():
        try:
            return parse_search_query(llm, query, now=now, items=item_names)
        except LLMError:
            pass
    return ParsedQuery(item=query.strip(), anchor_text=None, anchor_time=None)


def _resolve_item(conn, parsed_item: str, raw_query: str) -> db.Item | None:
    """Match a parsed/typed item name to a tracked item; ``None`` if nothing fits.

    Exact (case-insensitive) name first, then a loose containment match either way so
    "where are my keys" lands on the tracked "Keys" even when the model is offline.
    """
    exact = db.get_item_by_name(conn, parsed_item)
    if exact is not None:
        return exact
    needle = parsed_item.strip().lower()
    haystack = raw_query.strip().lower()
    for item in db.list_items(conn):
        name = item.name.lower()
        if name in haystack or (needle and (needle in name or name in needle)):
            return item
    return None


# --- surface hint -------------------------------------------------------------


def _surface_hint(conn, zone_id: str, prefer_surface_id: str | None = None) -> str | None:
    """Name of a surface to mention as a where-to-look hint, or ``None``.

    Prefers an explicitly known surface (the item's home surface); otherwise a
    representative surface for the zone. Surfaces only decorate — never rank.
    """
    surface_id = prefer_surface_id or db.first_surface_id(conn, zone_id)
    if surface_id is None:
        return None
    surface = db.get_surface(conn, surface_id)
    return surface.name if surface else None


# --- query box ----------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
def query_form(
    request: Request,
    conn=Depends(get_conn),
    templates: Jinja2Templates = Depends(get_templates),
):
    """Step 1: ask what's missing and roughly when it was last had."""
    items = db.list_items(conn)
    return templates.TemplateResponse(
        request, "find/query.html", {"items": items, "has_items": bool(items)}
    )


@router.post("", response_class=HTMLResponse)
def query_submit(
    request: Request,
    query: str = Form(""),
    item_id: str = Form(""),
    conn=Depends(get_conn),
    llm: LLMClient = Depends(get_llm),
    templates: Jinja2Templates = Depends(get_templates),
):
    """Parse the query, open a search, and show the first place worth checking.

    An explicit ``item_id`` (picked from the list) wins over the parsed item name; the
    free-text box is still parsed for the memory anchor either way.
    """
    now = datetime.now()
    items = db.list_items(conn)
    parsed = _parse_query(llm, query, now=now, item_names=[i.name for i in items])

    item = db.get_item(conn, item_id) if item_id else None
    if item is None:
        item = _resolve_item(conn, parsed.item, query)
    if item is None:
        # Couldn't tell which item — re-ask with an explicit picker.
        return templates.TemplateResponse(
            request,
            "find/query.html",
            {"items": items, "has_items": bool(items), "unresolved": query.strip()},
        )

    search_id = db.create_search(
        conn,
        item.id,
        anchor_claim_text=parsed.anchor_text,
        anchor_time=parsed.anchor_time,
        now=now,
    )
    return _render_first_pass(request, templates, conn, search_id, item, parsed)


def _render_first_pass(
    request: Request,
    templates: Jinja2Templates,
    conn,
    search_id: int,
    item: db.Item,
    parsed: ParsedQuery,
) -> HTMLResponse:
    """Show the home-prior suggestion ("usually lives at X — checked there?").

    Records that suggestion so a rejection counts toward places-checked. When there's no
    learned home yet, there's nothing to check first, so we hand straight to retracing.
    """
    priors = db.read_priors(conn, item.id)
    first = first_pass_suggestion(item, priors=priors)
    if first is None:
        return templates.TemplateResponse(
            request,
            "find/first_pass.html",
            {"search_id": search_id, "item": item, "home": None, "anchor": parsed.anchor_text},
        )

    home_zone = db.get_zone(conn, first.zone_id)
    surface = _surface_hint(conn, first.zone_id, first.surface_id)
    db.add_suggestion(
        conn,
        search_id,
        first.zone_id,
        rank=1,
        surface_id=first.surface_id or db.first_surface_id(conn, first.zone_id),
        reason="usual spot",
    )
    return templates.TemplateResponse(
        request,
        "find/first_pass.html",
        {
            "search_id": search_id,
            "item": item,
            "home": home_zone,
            "surface": surface,
            "anchor": parsed.anchor_text,
        },
    )


# --- rejection -> retrospective timeline interview ----------------------------


def _home_zone_id(conn, search: db.Search) -> str | None:
    """The item's learned/declared home zone, the one excluded from the rejection pass."""
    item = db.get_item(conn, search.item_id)
    if item is None:
        return None
    priors = db.read_priors(conn, search.item_id)
    first = first_pass_suggestion(item, priors=priors)
    return first.zone_id if first else None


def _interview_zones(conn, exclude_zone_id: str | None):
    """Dwell zones to offer in the retrace, minus the already-ruled-out home spot."""
    return [z for z in db.list_dwell_zones(conn) if z.id != exclude_zone_id]


@router.post("/{search_id}/reject-home", response_class=HTMLResponse)
def reject_home(
    search_id: int,
    request: Request,
    conn=Depends(get_conn),
    templates: Jinja2Templates = Depends(get_templates),
):
    """User says it's not in the usual spot: rule that out, then start the retrace."""
    search = db.get_search(conn, search_id)
    if search is None:
        return RedirectResponse("/find", status_code=303)
    item = db.get_item(conn, search.item_id)

    # Mark any standing (un-rejected) first-pass suggestion as ruled out.
    for s in db.list_suggestions(conn, search_id):
        if not s["rejected"]:
            db.reject_suggestion(conn, s["id"])

    home_id = _home_zone_id(conn, search)
    zones = _interview_zones(conn, home_id)
    return templates.TemplateResponse(
        request,
        "find/interview.html",
        {
            "search_id": search_id,
            "item": item,
            "zones": zones,
            "anchor": search.anchor_claim_text,
        },
    )


def _effective_anchor(anchor_time: datetime | None, now: datetime) -> datetime:
    """A usable retrace start: the stated anchor if it's in the past, else a default look-back."""
    if anchor_time is not None and anchor_time < now:
        return anchor_time
    return now - _DEFAULT_LOOKBACK


def _synthesize_timeline(
    selections: list[tuple[str, float]], start: datetime, now: datetime
) -> list[DwellEntry]:
    """Turn the retrace answers into contiguous dwell intervals across ``[start, now]``.

    Each chosen zone gets a slice of the elapsed time proportional to its coarse dwell
    weight, in the order given. This is the retrospective half of the hybrid stub — it
    produces exactly the ``(zone, enter, exit)`` shape the engine consumes, so the engine
    never learns it wasn't a sensor.
    """
    total_weight = sum(w for _, w in selections)
    if total_weight <= 0 or now <= start:
        return []
    span = (now - start).total_seconds()
    entries: list[DwellEntry] = []
    cursor = start
    for zone_id, weight in selections:
        seconds = span * (weight / total_weight)
        nxt = cursor + timedelta(seconds=seconds)
        entries.append(DwellEntry(zone_id=zone_id, enter=cursor, exit=nxt))
        cursor = nxt
    return entries


@router.post("/{search_id}/timeline", response_class=HTMLResponse)
async def timeline_submit(
    search_id: int,
    request: Request,
    conn=Depends(get_conn),
    llm: LLMClient = Depends(get_llm),
    templates: Jinja2Templates = Depends(get_templates),
):
    """Reconstruct the timeline from the retrace, then show ranked places to look."""
    search = db.get_search(conn, search_id)
    if search is None:
        return RedirectResponse("/find", status_code=303)
    item = db.get_item(conn, search.item_id)
    home_id = _home_zone_id(conn, search)

    form = await request.form()
    selections: list[tuple[str, float]] = []
    for zone_id in form.getlist("zone"):
        zone_id = str(zone_id)
        weight = _DWELL_WEIGHTS.get(str(form.get(f"dwell_{zone_id}", "while")), 2.0)
        selections.append((zone_id, weight))

    if not selections:
        # Nothing selected — gently re-ask rather than rank on no evidence.
        zones = _interview_zones(conn, home_id)
        return templates.TemplateResponse(
            request,
            "find/interview.html",
            {
                "search_id": search_id,
                "item": item,
                "zones": zones,
                "anchor": search.anchor_claim_text,
                "needs_pick": True,
            },
        )

    now = datetime.now()
    start = _effective_anchor(search.anchor_time, now)
    for entry in _synthesize_timeline(selections, start, now):
        db.add_dwell_entry(conn, entry.zone_id, entry.enter, entry.exit)

    # Items live in dwell zones; transit spaces (a hallway) hold nothing, so they're the
    # plausible set even when adjacency widening would otherwise float one up.
    dwell_ids = {z.id for z in db.list_dwell_zones(conn)}
    scored = rank_zones(
        item=item,
        timeline=db.read_timeline(conn),
        anchor_time=start,
        now=now,
        failure_modes=db.read_failure_modes(conn, search.item_id),
        plausible_zone_ids=dwell_ids,
        excluded_zone_ids={home_id} if home_id else None,
        claimed_zone_id=home_id,
        adjacency=db.adjacency_map(conn),
    )
    return _render_suggestions(request, templates, conn, llm, search_id, item, scored)


# --- ranked suggestions (engine ranks, LLM phrases) ---------------------------


def _grounding(item_name: str, zone_name: str, sz: ScoredZone, top_dwell: float) -> str:
    """The one factual reason the engine picked this zone — the only thing the LLM may use."""
    if sz.failure_weight > 0:
        return (
            f"the {item_name} has turned up in the {zone_name} before "
            "when it wasn't in its usual spot"
        )
    if sz.dwell_seconds > 0 and sz.dwell_seconds >= top_dwell:
        return f"you spent the most time in the {zone_name} since you last had the {item_name}"
    if sz.dwell_seconds > 0:
        return f"you spent some time in the {zone_name} since then"
    return f"the {zone_name} is right next to where the {item_name} usually lives"


def _fallback_reason(zone_name: str, surface: str | None, grounding: str) -> str:
    """A warm, soft reason without the model — used when phrasing is offline."""
    reason = f"{grounding[0].upper()}{grounding[1:]} — good chance it's there"
    if surface:
        reason += f", maybe on the {surface}"
    return reason + "."


def _render_suggestions(
    request: Request,
    templates: Jinja2Templates,
    conn,
    llm: LLMClient,
    search_id: int,
    item: db.Item,
    scored: list[ScoredZone],
) -> HTMLResponse:
    """Phrase each engine-ranked zone, persist the suggestions, and render them tappable."""
    start_rank = len(db.list_suggestions(conn, search_id)) + 1
    top_dwell = max((sz.dwell_seconds for sz in scored), default=0.0)
    online = llm.is_available()

    rows = []
    for offset, sz in enumerate(scored):
        rank = start_rank + offset
        zone = db.get_zone(conn, sz.zone_id)
        zone_name = zone.name if zone else sz.zone_id
        # Spec: only the top suggestion names a surface hint.
        surface_id = db.first_surface_id(conn, sz.zone_id) if offset == 0 else None
        surface = (
            db.get_surface(conn, surface_id).name if surface_id else None
        )
        grounding = _grounding(item.name, zone_name, sz, top_dwell)
        reason = _fallback_reason(zone_name, surface, grounding)
        if online:
            try:
                phrased = phrase_reason(
                    llm, item=item.name, zone=zone_name, grounding=grounding, surface=surface
                )
                if phrased.strip():  # an empty model reply keeps the warm fallback
                    reason = phrased
            except LLMError:
                pass
        suggestion_id = db.add_suggestion(
            conn, search_id, sz.zone_id, rank, surface_id=surface_id, reason=reason
        )
        rows.append(
            {
                "id": suggestion_id,
                "rank": rank,
                "zone": zone,
                "surface": surface,
                "reason": reason,
            }
        )

    return templates.TemplateResponse(
        request,
        "find/suggestions.html",
        {"search_id": search_id, "item": item, "rows": rows},
    )


__all__ = ["router"]
