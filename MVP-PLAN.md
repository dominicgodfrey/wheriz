# Where-Was-I-When — MVP Progress Tracker

> **This file is the living tracker** (the *where we are*). It is the session-to-session
> memory of the project — read it at the start of every session.
> Design reference (decisions, architecture, data model, scoring spec): [MVP.md](MVP.md).
> Full product concept: [item-finder-project-plan.md](item-finder-project-plan.md).

## Current Phase

**M1 — Schema + engine core.**

## Active Sub-Task

Building the deterministic engine core (`engine/`) as pure functions with synthetic-fixture
pytest coverage, then the SQLite schema. Keystone deliverable: the 5-synthetic-loss test
where rank-of-actual demonstrably improves.

## Completed

- [x] **M0 — Environment & repo** (commit: `06acc3f`) — `git init`, `.gitignore` (excludes `data/`
      entirely), `pyproject.toml` (FastAPI/uvicorn/jinja2/httpx; pytest dev), project `CLAUDE.md`
      with privacy rules, package stubs (`src/wwiw/__init__.py`, `src/wwiw/engine/__init__.py`).
      _Ollama install / model pull not verified in-repo (machine has RTX 5060 Ti 16GB)._

## In Progress

- [ ] **M1 — Schema + engine core**
      Status: starting. Building in atomic, pushed-per-change increments:
        1. engine `types.py` (domain dataclasses)
        2. engine `memory.py` (anchor widening + claimed-vs-actual observation)
        3. engine `scoring.py` (deterministic zone ranking)
        4. engine `learning.py` (prior + failure-mode updates)
        5. keystone integration test (5 losses, ranking improves)
        6. `db.py` (SQLite schema + access; append-only finds/searches)
      Blockers: none.

## Next Up

- [ ] **M2 — LLM edge layer** (Ollama client, prompt files, 5 parse/phrase tasks, call logging)
- [ ] **M3 — Onboarding wizard** (rooms → photos → loss interview)
- [ ] **M4 — Search + find loop** (the highest-leverage UX)
- [ ] **M5 — Quick dwell-log + stats view + full-wipe**
- [ ] **M6 — Dogfood** until 5 real losses resolve with visible ranking improvement

## How to Verify Completed Work

- M0: repo builds (`pip install -e .[dev]`); `git status` clean of `data/`.
- M1 (as it lands): `python -m pytest` — engine invariants (scores normalized, negative-space
  zones never suggested, ranking-improves-over-5-losses test) and schema invariants
  (append-only finds, FK integrity).

## Notes / Decisions Log

_Structural facts only — never real goal/memory/residence content (privacy rule)._

- 2026-06-19: Split the former single `MVP-PLAN.md` into two files — `MVP.md` (static design
  reference) and this `MVP-PLAN.md` (living tracker), so the tracker stays terse and the spec
  stays stable. `CLAUDE.md` updated to point at both.
- 2026-06-19: M1 engine modules are **pure** (no DB/LLM imports) per the "deterministic core"
  rule; they operate on `engine/types.py` dataclasses. `db.py` is the only side-effect boundary.
  M1 development proceeds as atomic commits, each tested and pushed to origin/main.
