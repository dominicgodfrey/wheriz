"""The five LLM edge tasks: parse user language in, phrase one reason out.

Each task renders a versioned prompt, calls the injected ``LLMClient``, and maps the raw
response into a small typed result the rest of the app can consume. The LLM never ranks
or decides — parse tasks extract structure the user will review/edit before anything is
saved, and ``phrase_reason`` only words a decision the deterministic engine already made.

Results are returned as plain dataclasses (not engine/DB types) because every parse here
is provisional: onboarding shows the user the parsed rooms/items/surfaces to confirm, and
the search parse feeds the timeline interview. The glue layer maps these to DB rows.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from .client import LLMClient, LLMError

_VALID_KINDS = {"dwell", "transit"}


# --- result types -------------------------------------------------------------


@dataclass(frozen=True)
class ParsedZone:
    """A room parsed from the residence description. ``kind`` is dwell|transit."""

    name: str
    kind: str = "dwell"


@dataclass(frozen=True)
class ParsedResidence:
    """The room graph: zones plus undirected adjacency edges (name pairs)."""

    zones: list[ParsedZone] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedItem:
    """A trackable item with a moderate-confidence starting prior for its home room."""

    name: str
    home_zone: str | None = None
    confidence: float = 0.5


@dataclass(frozen=True)
class ParsedQuery:
    """A parsed 'where is my X?' request and its optional resolved memory anchor."""

    item: str
    anchor_text: str | None = None
    anchor_time: datetime | None = None


# --- tasks --------------------------------------------------------------------


def parse_residence(client: LLMClient, description: str) -> ParsedResidence:
    """NL residence description -> room graph (user reviews before save)."""
    from .prompts import render

    raw = client.generate_text(
        render("parse_residence", description=description),
        task="parse_residence",
        format="json",
    )
    data = _extract_json(raw)
    zones: list[ParsedZone] = []
    for z in data.get("zones", []):
        name = str(z.get("name", "")).strip()
        if not name:
            continue
        kind = str(z.get("kind", "dwell")).strip().lower()
        zones.append(ParsedZone(name=name, kind=kind if kind in _VALID_KINDS else "dwell"))
    edges: list[tuple[str, str]] = []
    for e in data.get("edges", []):
        if isinstance(e, (list, tuple)) and len(e) == 2 and all(str(x).strip() for x in e):
            edges.append((str(e[0]).strip(), str(e[1]).strip()))
    return ParsedResidence(zones=zones, edges=edges)


def parse_loss_interview(
    client: LLMClient, answers: str, zones: Sequence[str]
) -> list[ParsedItem]:
    """Loss-interview answer -> items + moderate-confidence home priors."""
    from .prompts import render

    raw = client.generate_text(
        render("parse_loss_interview", answers=answers, zones=_bullets(zones)),
        task="parse_loss_interview",
        format="json",
    )
    data = _extract_json(raw)
    items: list[ParsedItem] = []
    for it in data.get("items", []):
        name = str(it.get("name", "")).strip()
        if not name:
            continue
        home = it.get("home_zone")
        home_zone = str(home).strip() if home not in (None, "") else None
        items.append(
            ParsedItem(name=name, home_zone=home_zone, confidence=_clamp01(it.get("confidence")))
        )
    return items


def extract_surfaces(
    client: LLMClient, images: Sequence[bytes], zone_name: str
) -> list[str]:
    """Room photo(s) -> surface inventory (user prunes the list)."""
    from .prompts import render

    raw = client.generate_vision(
        render("extract_surfaces", zone_name=zone_name),
        images,
        task="extract_surfaces",
        format="json",
    )
    data = _extract_json(raw)
    seen: set[str] = set()
    surfaces: list[str] = []
    for s in data.get("surfaces", []):
        name = str(s).strip()
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            surfaces.append(name)
    return surfaces


def parse_search_query(
    client: LLMClient, query: str, *, now: datetime, items: Sequence[str]
) -> ParsedQuery:
    """'Where is my X (since when)?' -> item + optional resolved anchor time."""
    from .prompts import render

    raw = client.generate_text(
        render(
            "parse_search_query",
            query=query,
            now=now.isoformat(timespec="seconds"),
            items=_bullets(items),
        ),
        task="parse_search_query",
        format="json",
    )
    data = _extract_json(raw)
    item = str(data.get("item", "")).strip() or query.strip()
    anchor = data.get("anchor_text")
    anchor_text = str(anchor).strip() if anchor not in (None, "") else None
    return ParsedQuery(
        item=item,
        anchor_text=anchor_text,
        anchor_time=_parse_iso(data.get("anchor_time")),
    )


def phrase_reason(
    client: LLMClient,
    *,
    item: str,
    zone: str,
    grounding: str,
    surface: str | None = None,
) -> str:
    """Word ONE warm, probabilistic suggestion reason — the math already decided."""
    from .prompts import render

    raw = client.generate_text(
        render(
            "phrase_reason",
            item=item,
            zone=zone,
            grounding=grounding,
            surface=surface or "none",
        ),
        task="phrase_reason",
    )
    return _one_line(raw)


# --- parsing helpers ----------------------------------------------------------


def _extract_json(text: str):
    """Parse JSON from a model response, tolerating code fences and surrounding prose."""
    s = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.DOTALL)
    if fenced:
        s = fenced.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Last resort: slice from the first opener to the last matching closer.
    starts = [i for i in (s.find("{"), s.find("[")) if i != -1]
    ends = [i for i in (s.rfind("}"), s.rfind("]")) if i != -1]
    if starts and ends:
        candidate = s[min(starts) : max(ends) + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    raise LLMError(f"Could not parse JSON from model response: {text[:200]!r}")


def _parse_iso(value) -> datetime | None:
    """Parse an ISO-8601 timestamp, tolerating a trailing 'Z'. None on anything else."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _clamp01(value) -> float:
    """Coerce to a [0,1] confidence; default 0.5 when missing/garbage."""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


def _bullets(values: Sequence[str]) -> str:
    """Render a list as a markdown bullet block for prompt insertion."""
    items = [str(v).strip() for v in values if str(v).strip()]
    return "\n".join(f"- {v}" for v in items) if items else "(none yet)"


def _one_line(text: str) -> str:
    """Collapse a phrased reason to a single trimmed line, stripping wrapping quotes."""
    line = " ".join(text.split()).strip()
    if len(line) >= 2 and line[0] in "\"'" and line[-1] == line[0]:
        line = line[1:-1].strip()
    return line


__all__ = [
    "ParsedItem",
    "ParsedQuery",
    "ParsedResidence",
    "ParsedZone",
    "extract_surfaces",
    "parse_loss_interview",
    "parse_residence",
    "parse_search_query",
    "phrase_reason",
]
