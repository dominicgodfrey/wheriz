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

import sqlite3
from pathlib import Path

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


__all__ = ["DEFAULT_DB_PATH", "SCHEMA", "connect", "initialize"]
