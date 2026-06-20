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
from pathlib import Path

from .engine.types import Item, Surface, Zone

DEFAULT_DB_PATH = Path("data") / "wwiw.sqlite"

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
    conn = sqlite3.connect(db_path)
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


__all__ = [
    "DEFAULT_DB_PATH",
    "SCHEMA",
    "add_edge",
    "connect",
    "count_items",
    "count_zones",
    "create_item",
    "create_surface",
    "create_zone",
    "get_zone",
    "get_zone_by_name",
    "initialize",
    "list_dwell_zones",
    "list_edges",
    "list_items",
    "list_surfaces",
    "list_zones",
    "set_prior",
]
