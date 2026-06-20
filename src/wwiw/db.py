"""SQLite schema and connection handling — the only side-effect boundary.

The deterministic engine (``wwiw.engine``) is pure and never imports this module. The DB
holds the persistent state the engine reasons over (zones, items, priors, failure modes,
the occupancy timeline) plus the append-only history of searches and finds.

Append-only history is enforced in SQL, not just convention:

* ``finds`` and ``memory_log`` reject UPDATE and DELETE outright.
* ``searches`` reject DELETE (you never erase that a search happened) but allow UPDATE,
  since a search legitimately transitions ``open → found → expired`` and records its
  follow-up. History is never erased; lifecycle state may advance.

A full wipe is "delete ``data/``" — there is no in-app destructive reset to maintain.
Datetimes are stored as ISO-8601 text; the engine works in ``datetime`` and the glue
layer converts at this boundary.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .engine.types import DwellEntry, Item, Surface, Zone

DEFAULT_DB_PATH = Path("data") / "wwiw.sqlite"


@dataclass(frozen=True)
class Search:
    """A find-loop search row. History, not engine vocabulary — lives at the DB boundary.

    ``anchor_time`` is the resolved memory anchor (when the user last knew where the item
    was); ``status`` advances ``open -> found -> expired`` and ``followed_up`` flips once
    the next-app-open prompt has asked about it.
    """

    id: int
    item_id: str
    anchor_claim_text: str | None
    anchor_time: datetime | None
    status: str
    followed_up: bool
    created_at: datetime | None

SCHEMA = """
-- Spatial graph -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS zones (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'dwell' CHECK (kind IN ('dwell', 'transit'))
);

CREATE TABLE IF NOT EXISTS zone_edges (
    a_zone_id TEXT NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    b_zone_id TEXT NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    PRIMARY KEY (a_zone_id, b_zone_id),
    CHECK (a_zone_id <> b_zone_id)
);

CREATE TABLE IF NOT EXISTS surfaces (
    id      TEXT PRIMARY KEY,
    zone_id TEXT NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    source  TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('photo', 'manual'))
);

-- Items and learned distributions ------------------------------------------
CREATE TABLE IF NOT EXISTS items (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    home_zone_id    TEXT REFERENCES zones(id) ON DELETE SET NULL,
    home_surface_id TEXT REFERENCES surfaces(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS priors (
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    zone_id TEXT NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    weight  REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (item_id, zone_id)
);

CREATE TABLE IF NOT EXISTS failure_modes (
    item_id        TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    zone_id        TEXT NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    count          INTEGER NOT NULL DEFAULT 0,
    decayed_weight REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (item_id, zone_id)
);

-- Occupancy timeline (the sacred interface) --------------------------------
CREATE TABLE IF NOT EXISTS dwell_entries (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    zone_id TEXT NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    enter   TEXT NOT NULL,
    exit    TEXT NOT NULL,
    source  TEXT NOT NULL DEFAULT 'retrospective'
            CHECK (source IN ('retrospective', 'quicklog'))
);

-- Search / find history (append-only) --------------------------------------
CREATE TABLE IF NOT EXISTS searches (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id           TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    anchor_claim_text TEXT,
    anchor_time       TEXT,
    status            TEXT NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open', 'found', 'expired')),
    followed_up       INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suggestions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id  INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    zone_id    TEXT NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    surface_id TEXT REFERENCES surfaces(id) ON DELETE SET NULL,
    rank       INTEGER NOT NULL,
    reason     TEXT,
    rejected   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS finds (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id          INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    zone_id            TEXT NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    surface_id         TEXT REFERENCES surfaces(id) ON DELETE SET NULL,
    was_suggested_rank INTEGER,
    places_checked     INTEGER,
    created_at         TEXT NOT NULL
);

-- Silent memory-trust log (claimed vs actual) ------------------------------
CREATE TABLE IF NOT EXISTS memory_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id      INTEGER REFERENCES searches(id) ON DELETE CASCADE,
    claimed_anchor TEXT,
    actual_outcome TEXT,
    created_at     TEXT NOT NULL
);

-- Append-only enforcement --------------------------------------------------
CREATE TRIGGER IF NOT EXISTS finds_no_update
    BEFORE UPDATE ON finds
    BEGIN SELECT RAISE(ABORT, 'finds are append-only'); END;

CREATE TRIGGER IF NOT EXISTS finds_no_delete
    BEFORE DELETE ON finds
    BEGIN SELECT RAISE(ABORT, 'finds are append-only'); END;

CREATE TRIGGER IF NOT EXISTS memory_log_no_update
    BEFORE UPDATE ON memory_log
    BEGIN SELECT RAISE(ABORT, 'memory_log is append-only'); END;

CREATE TRIGGER IF NOT EXISTS memory_log_no_delete
    BEFORE DELETE ON memory_log
    BEGIN SELECT RAISE(ABORT, 'memory_log is append-only'); END;

CREATE TRIGGER IF NOT EXISTS searches_no_delete
    BEFORE DELETE ON searches
    BEGIN SELECT RAISE(ABORT, 'searches are append-only (status may advance, rows are never deleted)'); END;
"""


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a connection with foreign keys on and the schema applied (idempotent).

    Pass ``":memory:"`` for tests. For file paths the parent directory (e.g. ``data/``)
    is created on demand; it is gitignored, so this never touches version control.
    """
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: each request opens (and closes) its own connection, but the
    # web framework may create it in a threadpool worker and use it on the event loop —
    # never concurrently. Disabling the guard makes that handoff safe.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    initialize(conn)
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    """Create all tables and append-only triggers if they do not already exist."""
    conn.executescript(SCHEMA)
    conn.commit()


# --- ID helpers ---------------------------------------------------------------


def _slugify(name: str) -> str:
    """Lowercase, hyphenated, URL-safe token derived from a display name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "x"


def _unique_id(conn: sqlite3.Connection, table: str, base: str) -> str:
    """A primary-key id starting from ``base``, suffixed ``-2``, ``-3`` … if taken.

    ``table`` is an internal constant, never user input, so the interpolation is safe.
    """
    candidate = base
    n = 2
    while conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (candidate,)).fetchone():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


# --- datetime <-> ISO text at the boundary ------------------------------------


def _to_iso(value: datetime | None) -> str | None:
    """Serialize a datetime to ISO-8601 text for storage. ``None`` passes through."""
    return value.isoformat() if value is not None else None


def _from_iso(value) -> datetime | None:
    """Parse ISO-8601 text back to a datetime, tolerating a trailing 'Z'. ``None`` on junk."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


# --- Onboarding writes (spatial graph + items + priors) -----------------------


def create_zone(conn: sqlite3.Connection, name: str, kind: str = "dwell") -> str:
    """Insert a zone and return its generated id. Invalid kinds fall back to dwell."""
    if kind not in ("dwell", "transit"):
        kind = "dwell"
    zone_id = _unique_id(conn, "zones", _slugify(name))
    conn.execute("INSERT INTO zones (id, name, kind) VALUES (?, ?, ?)", (zone_id, name, kind))
    conn.commit()
    return zone_id


def add_edge(conn: sqlite3.Connection, a_zone_id: str, b_zone_id: str) -> None:
    """Record an undirected doorway between two zones. Idempotent; self-edges ignored.

    Stored in sorted order so ``(a, b)`` and ``(b, a)`` collapse to one row.
    """
    if a_zone_id == b_zone_id:
        return
    lo, hi = sorted((a_zone_id, b_zone_id))
    conn.execute(
        "INSERT OR IGNORE INTO zone_edges (a_zone_id, b_zone_id) VALUES (?, ?)", (lo, hi)
    )
    conn.commit()


def create_surface(
    conn: sqlite3.Connection, zone_id: str, name: str, source: str = "manual"
) -> str:
    """Insert a surface within a zone and return its id. Source is photo|manual."""
    if source not in ("photo", "manual"):
        source = "manual"
    surface_id = _unique_id(conn, "surfaces", f"{zone_id}-{_slugify(name)}")
    conn.execute(
        "INSERT INTO surfaces (id, zone_id, name, source) VALUES (?, ?, ?, ?)",
        (surface_id, zone_id, name, source),
    )
    conn.commit()
    return surface_id


def create_item(
    conn: sqlite3.Connection,
    name: str,
    home_zone_id: str | None = None,
    home_surface_id: str | None = None,
) -> str:
    """Insert a trackable item and return its id."""
    item_id = _unique_id(conn, "items", _slugify(name))
    conn.execute(
        "INSERT INTO items (id, name, home_zone_id, home_surface_id) VALUES (?, ?, ?, ?)",
        (item_id, name, home_zone_id, home_surface_id),
    )
    conn.commit()
    return item_id


def set_prior(conn: sqlite3.Connection, item_id: str, zone_id: str, weight: float) -> None:
    """Upsert a home-location prior weight for ``(item, zone)``."""
    conn.execute(
        "INSERT INTO priors (item_id, zone_id, weight) VALUES (?, ?, ?) "
        "ON CONFLICT(item_id, zone_id) DO UPDATE SET weight = excluded.weight",
        (item_id, zone_id, float(weight)),
    )
    conn.commit()


# --- Reads (rows mapped to engine dataclasses) --------------------------------


def list_zones(conn: sqlite3.Connection) -> list[Zone]:
    """All zones, ordered by name."""
    rows = conn.execute("SELECT id, name, kind FROM zones ORDER BY name").fetchall()
    return [Zone(id=r["id"], name=r["name"], kind=r["kind"]) for r in rows]


def list_dwell_zones(conn: sqlite3.Connection) -> list[Zone]:
    """Dwell zones only — transit spaces don't hold items, so they get no surfaces."""
    rows = conn.execute(
        "SELECT id, name, kind FROM zones WHERE kind = 'dwell' ORDER BY name"
    ).fetchall()
    return [Zone(id=r["id"], name=r["name"], kind=r["kind"]) for r in rows]


def get_zone(conn: sqlite3.Connection, zone_id: str) -> Zone | None:
    """A single zone by id, or ``None``."""
    row = conn.execute("SELECT id, name, kind FROM zones WHERE id = ?", (zone_id,)).fetchone()
    return Zone(id=row["id"], name=row["name"], kind=row["kind"]) if row else None


def get_zone_by_name(conn: sqlite3.Connection, name: str) -> Zone | None:
    """First zone matching a display name (case-insensitive), or ``None``."""
    row = conn.execute(
        "SELECT id, name, kind FROM zones WHERE name = ? COLLATE NOCASE ORDER BY id LIMIT 1",
        (name,),
    ).fetchone()
    return Zone(id=row["id"], name=row["name"], kind=row["kind"]) if row else None


def list_edges(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """All undirected zone adjacencies as ``(a, b)`` id pairs."""
    rows = conn.execute(
        "SELECT a_zone_id, b_zone_id FROM zone_edges ORDER BY a_zone_id, b_zone_id"
    ).fetchall()
    return [(r["a_zone_id"], r["b_zone_id"]) for r in rows]


def list_surfaces(conn: sqlite3.Connection, zone_id: str) -> list[Surface]:
    """Surfaces within a zone, ordered by name."""
    rows = conn.execute(
        "SELECT id, zone_id, name, source FROM surfaces WHERE zone_id = ? ORDER BY name",
        (zone_id,),
    ).fetchall()
    return [
        Surface(id=r["id"], zone_id=r["zone_id"], name=r["name"], source=r["source"])
        for r in rows
    ]


def list_items(conn: sqlite3.Connection) -> list[Item]:
    """All trackable items, ordered by name."""
    rows = conn.execute(
        "SELECT id, name, home_zone_id, home_surface_id FROM items ORDER BY name"
    ).fetchall()
    return [
        Item(
            id=r["id"],
            name=r["name"],
            home_zone_id=r["home_zone_id"],
            home_surface_id=r["home_surface_id"],
        )
        for r in rows
    ]


def count_zones(conn: sqlite3.Connection) -> int:
    """Number of zones — used to tell whether onboarding's room step is done."""
    return conn.execute("SELECT COUNT(*) FROM zones").fetchone()[0]


def count_items(conn: sqlite3.Connection) -> int:
    """Number of items — used to tell whether the loss-interview step is done."""
    return conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]


def get_item(conn: sqlite3.Connection, item_id: str) -> Item | None:
    """A single item by id, or ``None``."""
    row = conn.execute(
        "SELECT id, name, home_zone_id, home_surface_id FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    return (
        Item(
            id=row["id"],
            name=row["name"],
            home_zone_id=row["home_zone_id"],
            home_surface_id=row["home_surface_id"],
        )
        if row
        else None
    )


def get_item_by_name(conn: sqlite3.Connection, name: str) -> Item | None:
    """First item whose name matches (case-insensitive), or ``None``.

    Used to resolve a parsed search query back to a tracked item.
    """
    row = conn.execute(
        "SELECT id, name, home_zone_id, home_surface_id FROM items "
        "WHERE name = ? COLLATE NOCASE ORDER BY id LIMIT 1",
        (name,),
    ).fetchone()
    return (
        Item(
            id=row["id"],
            name=row["name"],
            home_zone_id=row["home_zone_id"],
            home_surface_id=row["home_surface_id"],
        )
        if row
        else None
    )


def get_surface(conn: sqlite3.Connection, surface_id: str) -> Surface | None:
    """A single surface by id, or ``None``."""
    row = conn.execute(
        "SELECT id, zone_id, name, source FROM surfaces WHERE id = ?", (surface_id,)
    ).fetchone()
    return (
        Surface(id=row["id"], zone_id=row["zone_id"], name=row["name"], source=row["source"])
        if row
        else None
    )


def first_surface_id(conn: sqlite3.Connection, zone_id: str) -> str | None:
    """A representative surface id for a zone (first by name), or ``None``.

    Surfaces only ever *decorate* a suggestion as a where-to-look hint — they never
    enter the ranking. The MVP has no surface-level scoring, so the first surface is a
    reasonable hint; the find still records whichever surface the user actually picks.
    """
    row = conn.execute(
        "SELECT id FROM surfaces WHERE zone_id = ? ORDER BY name LIMIT 1", (zone_id,)
    ).fetchone()
    return row["id"] if row else None


# --- Learned distributions (priors + failure modes) ---------------------------


def read_priors(conn: sqlite3.Connection, item_id: str) -> dict[str, float]:
    """The item's home-location prior as ``{zone_id: weight}``."""
    rows = conn.execute(
        "SELECT zone_id, weight FROM priors WHERE item_id = ?", (item_id,)
    ).fetchall()
    return {r["zone_id"]: r["weight"] for r in rows}


def write_priors(conn: sqlite3.Connection, item_id: str, priors: dict[str, float]) -> None:
    """Upsert the full prior distribution for an item (one ``set_prior`` per zone)."""
    for zone_id, weight in priors.items():
        conn.execute(
            "INSERT INTO priors (item_id, zone_id, weight) VALUES (?, ?, ?) "
            "ON CONFLICT(item_id, zone_id) DO UPDATE SET weight = excluded.weight",
            (item_id, zone_id, float(weight)),
        )
    conn.commit()


def read_failure_modes(conn: sqlite3.Connection, item_id: str) -> dict[str, float]:
    """The item's failure-mode memory as ``{zone_id: decayed_weight}`` (the ranked weight)."""
    rows = conn.execute(
        "SELECT zone_id, decayed_weight FROM failure_modes WHERE item_id = ?", (item_id,)
    ).fetchall()
    return {r["zone_id"]: r["decayed_weight"] for r in rows}


def write_failure_modes(
    conn: sqlite3.Connection,
    item_id: str,
    weights: dict[str, float],
    bumped_zone_id: str | None = None,
) -> None:
    """Upsert decayed failure-mode weights; bump the found zone's raw count by one.

    The engine moves the decayed weights; ``count`` is a plain tally of away-from-home
    finds, incremented only for ``bumped_zone_id`` (the zone just found at).
    """
    for zone_id, weight in weights.items():
        increment = 1 if zone_id == bumped_zone_id else 0
        conn.execute(
            "INSERT INTO failure_modes (item_id, zone_id, count, decayed_weight) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(item_id, zone_id) DO UPDATE SET "
            "decayed_weight = excluded.decayed_weight, count = count + ?",
            (item_id, zone_id, increment, float(weight), increment),
        )
    conn.commit()


def adjacency_map(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Undirected zone adjacency as ``{zone_id: [neighbor_id, ...]}`` (both directions)."""
    adjacency: dict[str, list[str]] = {}
    for a, b in list_edges(conn):
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)
    return adjacency


# --- Occupancy timeline (the sacred interface) --------------------------------


def add_dwell_entry(
    conn: sqlite3.Connection,
    zone_id: str,
    enter: datetime,
    exit: datetime,
    source: str = "retrospective",
) -> int:
    """Append one occupancy interval and return its row id. Source is retrospective|quicklog.

    This is the *only* way time enters the timeline; the engine reads these back as
    ``DwellEntry`` and never learns which source produced them.
    """
    if source not in ("retrospective", "quicklog"):
        source = "retrospective"
    cur = conn.execute(
        "INSERT INTO dwell_entries (zone_id, enter, exit, source) VALUES (?, ?, ?, ?)",
        (zone_id, _to_iso(enter), _to_iso(exit), source),
    )
    conn.commit()
    return int(cur.lastrowid)


def read_timeline(conn: sqlite3.Connection) -> list[DwellEntry]:
    """The whole occupancy timeline as ``DwellEntry`` records, ordered by entry time.

    The engine does its own windowing — this hands over the raw intervals only, keeping
    the timeline interface source-agnostic.
    """
    rows = conn.execute(
        "SELECT zone_id, enter, exit FROM dwell_entries ORDER BY enter"
    ).fetchall()
    out: list[DwellEntry] = []
    for r in rows:
        enter, exit = _from_iso(r["enter"]), _from_iso(r["exit"])
        if enter is not None and exit is not None:
            out.append(DwellEntry(zone_id=r["zone_id"], enter=enter, exit=exit))
    return out


# --- Searches (append-only history; status may advance) -----------------------


def _row_to_search(row: sqlite3.Row) -> Search:
    return Search(
        id=row["id"],
        item_id=row["item_id"],
        anchor_claim_text=row["anchor_claim_text"],
        anchor_time=_from_iso(row["anchor_time"]),
        status=row["status"],
        followed_up=bool(row["followed_up"]),
        created_at=_from_iso(row["created_at"]),
    )


def create_search(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    anchor_claim_text: str | None,
    anchor_time: datetime | None,
    now: datetime,
) -> int:
    """Open a new search and return its id. ``now`` stamps ``created_at``."""
    cur = conn.execute(
        "INSERT INTO searches (item_id, anchor_claim_text, anchor_time, status, created_at) "
        "VALUES (?, ?, ?, 'open', ?)",
        (item_id, anchor_claim_text, _to_iso(anchor_time), _to_iso(now)),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_search(conn: sqlite3.Connection, search_id: int) -> Search | None:
    """A single search by id, or ``None``."""
    row = conn.execute(
        "SELECT id, item_id, anchor_claim_text, anchor_time, status, followed_up, created_at "
        "FROM searches WHERE id = ?",
        (search_id,),
    ).fetchone()
    return _row_to_search(row) if row else None


def set_search_status(conn: sqlite3.Connection, search_id: int, status: str) -> None:
    """Advance a search's lifecycle status (``open`` -> ``found`` -> ``expired``)."""
    if status not in ("open", "found", "expired"):
        raise ValueError(f"invalid search status: {status!r}")
    conn.execute("UPDATE searches SET status = ? WHERE id = ?", (status, search_id))
    conn.commit()


def mark_followed_up(conn: sqlite3.Connection, search_id: int) -> None:
    """Flag that the next-app-open prompt has already asked about this search."""
    conn.execute("UPDATE searches SET followed_up = 1 WHERE id = ?", (search_id,))
    conn.commit()


def next_followup_search(conn: sqlite3.Connection) -> Search | None:
    """The oldest open search not yet followed up, or ``None``.

    Drives the single next-app-open nudge: ask once about an unresolved search, then it
    is marked followed up (and expired) so we never pester twice.
    """
    row = conn.execute(
        "SELECT id, item_id, anchor_claim_text, anchor_time, status, followed_up, created_at "
        "FROM searches WHERE status = 'open' AND followed_up = 0 ORDER BY id LIMIT 1"
    ).fetchone()
    return _row_to_search(row) if row else None


# --- Suggestions (the places considered for a search) -------------------------


def add_suggestion(
    conn: sqlite3.Connection,
    search_id: int,
    zone_id: str,
    rank: int,
    *,
    surface_id: str | None = None,
    reason: str | None = None,
) -> int:
    """Record a place put to the user for one search; return its row id."""
    cur = conn.execute(
        "INSERT INTO suggestions (search_id, zone_id, surface_id, rank, reason, rejected) "
        "VALUES (?, ?, ?, ?, ?, 0)",
        (search_id, zone_id, surface_id, rank, reason),
    )
    conn.commit()
    return int(cur.lastrowid)


def reject_suggestion(conn: sqlite3.Connection, suggestion_id: int) -> None:
    """Mark a suggested place as one the user said the item was not at."""
    conn.execute("UPDATE suggestions SET rejected = 1 WHERE id = ?", (suggestion_id,))
    conn.commit()


def get_suggestion(conn: sqlite3.Connection, suggestion_id: int) -> sqlite3.Row | None:
    """A single suggestion row by id, or ``None``."""
    return conn.execute(
        "SELECT id, search_id, zone_id, surface_id, rank, reason, rejected "
        "FROM suggestions WHERE id = ?",
        (suggestion_id,),
    ).fetchone()


def list_suggestions(conn: sqlite3.Connection, search_id: int) -> list[sqlite3.Row]:
    """All suggestions for a search, ordered by rank."""
    return conn.execute(
        "SELECT id, search_id, zone_id, surface_id, rank, reason, rejected "
        "FROM suggestions WHERE search_id = ? ORDER BY rank",
        (search_id,),
    ).fetchall()


def count_rejected_suggestions(conn: sqlite3.Connection, search_id: int) -> int:
    """How many places the user has already ruled out for this search."""
    return conn.execute(
        "SELECT COUNT(*) FROM suggestions WHERE search_id = ? AND rejected = 1",
        (search_id,),
    ).fetchone()[0]


# --- Finds + the silent memory-trust log (append-only) ------------------------


def record_find(
    conn: sqlite3.Connection,
    search_id: int,
    zone_id: str,
    *,
    surface_id: str | None = None,
    was_suggested_rank: int | None = None,
    places_checked: int | None = None,
    now: datetime,
) -> int:
    """Append a confirmed find and return its id. Finds are append-only (SQL-enforced)."""
    cur = conn.execute(
        "INSERT INTO finds (search_id, zone_id, surface_id, was_suggested_rank, "
        "places_checked, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (search_id, zone_id, surface_id, was_suggested_rank, places_checked, _to_iso(now)),
    )
    conn.commit()
    return int(cur.lastrowid)


def log_memory(
    conn: sqlite3.Connection,
    search_id: int,
    *,
    claimed_anchor: str | None,
    actual_outcome: str | None,
    now: datetime,
) -> int:
    """Append a silent claimed-vs-actual record. Never surfaced to the user as a score."""
    cur = conn.execute(
        "INSERT INTO memory_log (search_id, claimed_anchor, actual_outcome, created_at) "
        "VALUES (?, ?, ?, ?)",
        (search_id, claimed_anchor, actual_outcome, _to_iso(now)),
    )
    conn.commit()
    return int(cur.lastrowid)


__all__ = [
    "DEFAULT_DB_PATH",
    "SCHEMA",
    "Search",
    "add_dwell_entry",
    "add_edge",
    "add_suggestion",
    "adjacency_map",
    "connect",
    "count_items",
    "count_rejected_suggestions",
    "count_zones",
    "create_item",
    "create_search",
    "create_surface",
    "create_zone",
    "first_surface_id",
    "get_item",
    "get_item_by_name",
    "get_search",
    "get_suggestion",
    "get_surface",
    "get_zone",
    "get_zone_by_name",
    "initialize",
    "list_dwell_zones",
    "list_edges",
    "list_items",
    "list_suggestions",
    "list_surfaces",
    "list_zones",
    "log_memory",
    "mark_followed_up",
    "next_followup_search",
    "read_failure_modes",
    "read_priors",
    "read_timeline",
    "record_find",
    "reject_suggestion",
    "set_prior",
    "set_search_status",
    "write_failure_modes",
    "write_priors",
]
