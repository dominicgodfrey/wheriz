"""The find loop: query -> first-pass -> retrospective interview -> ranked suggestions
-> confirm -> learning. This module owns the highest-leverage UX in the app.

The deterministic engine decides everything that matters here; this layer only collects
the user's language (parsed at the edge), reconstructs the occupancy timeline from their
answers, and presents engine output as *suggestions* the user taps. Every place the user
rules out and every confirmed find is persisted append-only, and a confirmed find feeds
the learning update so the next search ranks better.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import db
from ..llm.client import LLMClient, LLMError
from ..llm.tasks import ParsedQuery, parse_search_query
from .deps import get_conn, get_llm, get_templates

router = APIRouter(prefix="/find")


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
    from ..engine.scoring import first_pass_suggestion

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


__all__ = ["router"]
