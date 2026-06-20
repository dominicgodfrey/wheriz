# Where-Was-I-When — MVP Progress Tracker

> **This file is the living tracker** (the *where we are*). It is the session-to-session
> memory of the project — read it at the start of every session.
> Design reference (decisions, architecture, data model, scoring spec): [MVP.md](MVP.md).
> Full product concept: [item-finder-project-plan.md](item-finder-project-plan.md).

## Current Phase

**M2 — LLM edge layer** (next). M1 complete.

## Active Sub-Task

None active. Next session: M2 — Ollama client + the five parse/phrase prompt tasks + call
logging. Note from M0: Ollama install / model pull still unverified in-repo; that gate
belongs to M2 startup (machine has RTX 5060 Ti 16GB).

## Completed

- [x] **M0 — Environment & repo** (commit: `06acc3f`) — `git init`, `.gitignore` (excludes `data/`
      entirely), `pyproject.toml` (FastAPI/uvicorn/jinja2/httpx; pytest dev), project `CLAUDE.md`
      with privacy rules, package stubs.
      _Ollama install / model pull not verified in-repo (machine has RTX 5060 Ti 16GB)._
- [x] **M1 — Schema + engine core** — deterministic core as pure functions + SQLite schema,
      56 passing tests, no live-LLM dependency. Built and pushed in atomic increments:
  - `engine/types.py` — domain dataclasses (commit: `40786e2`)
  - `engine/memory.py` — anchor widening + silent claimed-vs-actual (commit: `72cb141`)
  - `engine/scoring.py` — deterministic zone ranking (commit: `35ca350`)
  - `engine/learning.py` — prior + failure-mode updates (commit: `744d058`)
  - keystone test — rank-of-actual improves `[3,3,2,2,1]` over 5 losses (commit: `3db1bbe`)
  - `db.py` — SQLite schema, append-only finds/memory_log + no-delete searches (commit: `884617d`)

## Next Up

- [ ] **M2 — LLM edge layer** (Ollama client, prompt files, 5 parse/phrase tasks, call logging)
- [ ] **M3 — Onboarding wizard** (rooms → photos → loss interview)
- [ ] **M4 — Search + find loop** (the highest-leverage UX)
- [ ] **M5 — Quick dwell-log + stats view + full-wipe**
- [ ] **M6 — Dogfood** until 5 real losses resolve with visible ranking improvement

## How to Verify Completed Work

- M0: repo builds (`pip install -e .[dev]`); `git status` clean of `data/`.
- M1: `python -m pytest` (56 tests) — engine invariants (scores normalized, negative-space
  zones never suggested, `test_ranking_improves.py` 5-loss test) and schema invariants
  (`test_db.py`: append-only finds/memory_log, no-delete searches, FK + CHECK integrity).

## Notes / Decisions Log

_Structural facts only — never real goal/memory/residence content (privacy rule)._

- 2026-06-19: Split the former single `MVP-PLAN.md` into two files — `MVP.md` (static design
  reference) and this `MVP-PLAN.md` (living tracker), so the tracker stays terse and the spec
  stays stable. `CLAUDE.md` updated to point at both.
- 2026-06-19: M1 engine modules are **pure** (no DB/LLM imports) per the "deterministic core"
  rule; they operate on `engine/types.py` dataclasses. `db.py` is the only side-effect boundary.
  M1 development proceeds as atomic commits, each tested and pushed to origin/main.
- 2026-06-19: Scoring model — rejection-pass score `∝ (failure_weight + smoothing) ×
  normalized_dwell + adjacency_residual`, normalized over the kept (≤ `max_candidates`)
  candidates. Laplace `failure_smoothing` keeps cold-start dwelled zones rankable; adjacency
  residual is a small flat floor so a never-dwelled neighbor of the claimed zone can still
  surface (the one deliberate exception to negative-space pruning).
- 2026-06-19: Learning — home prior is a self-normalizing decaying average (sum stays 1.0);
  failure-mode memory only updates on an **away-from-home** find (home finds leave it
  untouched), so the failure signal isn't eroded by ordinary successful checks.
- 2026-06-19: Append-only interpretation — `finds` and `memory_log` reject UPDATE+DELETE;
  `searches` reject DELETE only (status `open→found→expired` and `followed_up` may advance).
  Enforced via SQLite triggers, surfacing as `sqlite3.IntegrityError`. Full wipe = delete
  `data/`; no in-app destructive reset.
