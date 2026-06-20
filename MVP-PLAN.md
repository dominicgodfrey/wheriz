# Where-Was-I-When — MVP Progress Tracker

> **This file is the living tracker** (the *where we are*). It is the session-to-session
> memory of the project — read it at the start of every session.
> Design reference (decisions, architecture, data model, scoring spec): [MVP.md](MVP.md).
> Full product concept: [item-finder-project-plan.md](item-finder-project-plan.md).

## Current Phase

**M4 — Search + find loop** (next). M0–M3 complete.

## Active Sub-Task

None active. Next session: M4 — the find loop (highest-leverage UX): query box + anchor →
retrospective timeline interview ("where have you been since?") → ranked tappable
suggestions (engine `rank_zones` + `phrase_reason`) → one-tap confirm / "none of these" +
free text → learning update + acknowledgment; next-app-open follow-up for open searches.
**Runtime note:** Ollama server is now running, but `ollama list` shows **no models pulled**
yet — `ollama pull` the text/vision models before live dogfooding (see `OPEN-ISSUES.md`).
Onboarding already degrades gracefully when the model is missing, so this isn't a blocker.

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
- [x] **M2 — LLM edge layer** — Ollama client, prompt files, 5 parse/phrase tasks, call
      logging. 37 new tests, none touching a live model. Built and pushed atomically:
  - `llm/client.py` — Ollama HTTP client, injectable transport, sanitized call logging (commit: `eb70a5d`)
  - `llm/prompts.py` + `prompts/*.txt` — one template per task, `string.Template` loader (commit: `6b5dfda`)
  - `llm/tasks.py` — the five parse/phrase tasks + JSON/anchor-time parsing (commit: `6159999`)
- [x] **M3 — Onboarding wizard (web)** — FastAPI + Jinja wizard (rooms → photos → loss
      interview), wiring the M2 tasks; persists to the M1 schema. 39 new tests (web suite
      uses a temp DB + fake model, never Ollama). Built and pushed atomically:
  - `OPEN-ISSUES.md` — issue tracker; opening item is model selection (commit: `1eac66a`)
  - `db.py` access layer — zones/edges/surfaces/items/priors + row→dataclass reads (commit: `a411c81`)
  - `web/` skeleton — `create_app` factory, deps, base layout, landing, `main.py` (commit: `655509a`)
  - step 1 rooms — NL → `parse_residence` → review → persist (commit: `d70ca58`)
  - step 2 photos — upload → `extract_surfaces` → prune → persist (commit: `0944bba`)
  - step 3 loss interview — items + seeded priors + done page + e2e flow test (commit: `55a2420`)
  - hardening — graceful fallback on any model failure, not just downtime (commit: `9478e4b`)

## Next Up

- [ ] **M4 — Search + find loop** (the highest-leverage UX)
- [ ] **M5 — Quick dwell-log + stats view + full-wipe**
- [ ] **M6 — Dogfood** until 5 real losses resolve with visible ranking improvement

## How to Verify Completed Work

- M0: repo builds (`pip install -e .[dev]`); `git status` clean of `data/`.
- M1: `python -m pytest` (56 tests) — engine invariants (scores normalized, negative-space
  zones never suggested, `test_ranking_improves.py` 5-loss test) and schema invariants
  (`test_db.py`: append-only finds/memory_log, no-delete searches, FK + CHECK integrity).
- M2: `python -m pytest tests/llm` (37 tests, no live model) — client payload shaping +
  sanitized logging (image bytes never logged raw), prompt loader (every template renders,
  no leftover `$placeholder`), and the five tasks against recorded synthetic outputs
  (fenced/prose JSON, clamped confidence, trailing-`Z` and unparseable anchor times).
- M3: `python -m pytest tests/web` (24) + `tests/test_db_access.py` (13) — wizard steps
  parse→review→persist, end-to-end flow leaves a consistent DB whose seeded prior makes the
  engine's first-pass land on the home, and every step degrades to manual entry on model
  failure. Live boot: `python -m wwiw.main` → `/`, `/onboarding/rooms`, `/healthz` all 200,
  and a real parse against Ollama-with-no-models returns 200 (fallback), not 500.

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
- 2026-06-20: M2 LLM client — HTTP transport is **injectable** (default lazily imports
  `httpx`); the whole suite injects a fake requester, so tests need neither `httpx` nor a
  live model. Call logs to `data/llm_logs/` are **sanitized**: vision image bytes are
  summarized (count + sizes), never written raw — photos stay in `data/photos/` only.
- 2026-06-20: M2 prompts — one `.txt` per task under `llm/prompts/`, loaded via
  `string.Template` (`$var`), chosen so the JSON `{}` braces in prompt bodies don't collide
  with placeholders; `substitute` raises on a missing var so under-filled prompts fail loud.
- 2026-06-20: M2 tasks return **provisional** dataclasses (`ParsedResidence`/`ParsedItem`/
  `ParsedQuery`/surface-name list), not engine/DB types — onboarding shows them for user
  confirm/edit before persistence. JSON extraction tolerates code fences + surrounding
  prose; anchor-time parsing tolerates trailing `Z` and degrades to `None` (never raises on
  a vague time). The LLM still never ranks — `phrase_reason` only words an engine decision.
- 2026-06-20: Ollama runtime status — binary installed; the **server is now running**
  (`/api/tags` answers) but `ollama list` shows **no models pulled** — `ollama pull` the
  text/vision models before live dogfooding (tracked in `OPEN-ISSUES.md`).
- 2026-06-20: M3 web layer — `create_app(db_path, llm_client)` factory so tests inject a
  temp DB + fake model (web suite never opens a socket to Ollama). SQLite opens with
  `check_same_thread=False` (per-request connection, never shared concurrently) to survive
  FastAPI's threadpool handoff. Starlette ≥1.3 `TemplateResponse(request, name, context)`
  signature is required — the old `(name, {"request": ...})` form crashes the template cache.
- 2026-06-20: M3 onboarding contract — every step is **parse → review/edit → confirm**; the
  LLM only proposes, the user confirms before anything persists. `is_available()` proves only
  that the server answered, so each parse step also catches `LLMError` and **falls back** to
  manual/line-split entry (verified live against Ollama-with-no-models). Room photos are read
  in memory for surface extraction and **never written to disk** (privacy); only surface
  names persist (`source = photo|manual`). Onboarding writes added to `db.py` (the side-effect
  boundary); web glue maps parsed names → ids. Dynamic form lists posted as repeated keys and
  read with `form.getlist` aligned by DOM order (clear a name to drop a row).
