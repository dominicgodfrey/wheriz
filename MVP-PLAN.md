# Where-Was-I-When — MVP Progress Tracker

> **This file is the living tracker** (the *where we are*). It is the session-to-session
> memory of the project — read it at the start of every session.
> Design reference (decisions, architecture, data model, scoring spec): [MVP.md](MVP.md).
> Full product concept: [item-finder-project-plan.md](item-finder-project-plan.md).

## Current Phase

**M6 — Dogfood** (next). M0–M5 complete.

## Active Sub-Task

None active. Next session: M6 — use it for real losses and fix friction until **five real
loss events resolve with visible ranking improvement** on the stats view (the exit
criterion). This is a usage/observation phase, not primarily a coding one: do real
onboarding on the actual residence, then log/find for real and watch the `/stats` trend.
**Blocker to clear first:** `ollama pull` the chosen text + vision models — the server is
running but `ollama list` shows **no models pulled** (see `OPEN-ISSUES.md` #1). The whole
app degrades gracefully with no model (every flow verified live at 200), so this only
gates *quality* of parsing/phrasing, not function. M5's `/log` and `/stats` don't touch
the LLM at all.

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
- [x] **M4 — Search + find loop (web)** — the highest-leverage UX, wiring the M1 engine
      (`first_pass_suggestion`/`rank_zones`/`apply_find`) and M2 edge tasks
      (`parse_search_query`/`phrase_reason`) into a full loop. 31 new tests (web suite +
      DB layer), all offline-capable. Built and pushed atomically:
  - `db.py` find-loop access layer — searches/suggestions/finds/memory_log/timeline +
    priors/failure-mode read-write, `Search` dataclass, ISO↔datetime (commit: `9c8361f`)
  - step 1 — query box → `parse_search_query` → first-pass home suggestion (commit: `28e3875`)
  - step 2 — reject home → retrace interview → `rank_zones` + phrased reasons (commit: `9ca273b`)
  - step 3 — confirm / none-of-these → find + learning + acknowledgment (commit: `cf3d11c`)
  - step 4 — next-app-open follow-up for open searches (commit: `f80aaab`)
- [x] **M5 — Quick dwell-log + stats view + full-wipe** — the hybrid stub's second half +
      instrumentation + the documented reset. 20 new tests, all offline-capable; live-boot
      smoke (real factory, Ollama offline) drives `/`, `/log`, `/log` POST, `/stats` all 200.
      Built and pushed atomically:
  - `db.py` reads — `recent_dwell_entries` (display-only) + `list_finds` (stats trend) (commit: `7eb3b90`)
  - quick dwell-log page — `/log`: room + coarse duration → `(zone, enter, exit, quicklog)` (commit: `9975cc6`)
  - stats view — `/stats`: pure `summarize_finds` trend of places-checked + rank (commit: `85517c1`)
  - full-wipe — documented (CLAUDE.md) + quiet stats-page note, no in-app button (commit: `02ed385`)

## Next Up

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
- M4: `tests/test_db_findloop.py` (16) — search/suggestion/find/memory/timeline writes incl.
  append-only enforcement, and priors/failure-mode round-trips. `tests/web/test_find_*` (25):
  query resolution + offline fallback, first-pass, the retrace→`rank_zones` ranking (dwell
  order, failure-memory override, transit exclusion), confirm→learning, the **canonical
  scenario** (two couch finds make the couch lead the next search), and the single follow-up.
  Live boot smoke: real `create_app` (Ollama offline) drives query→reject→retrace→confirm
  all 200 and the loop closes — graceful degradation with no model pulled.
- M5: `tests/test_db_findloop.py` (+4) — `recent_dwell_entries` end-ordering/name+source/limit
  and `list_finds` chronological-with-metrics / empty. `tests/web/test_log.py` (8): dwell-only
  room list, onboarding redirect, a quick-log write lands a `quicklog` interval ending now,
  longer dwell ⇒ longer span, missing room never writes, the recent panel echoes, and the
  logged dwell is read back through `read_timeline` (source-agnostic). `tests/web/test_stats.py`
  (10): pure `summarize_finds` (empty / too-few-for-trend / improving / slipping / steady / bar
  scaling) + rendered page (empty state, improving copy, the wipe instruction is present, and
  the memory-trust log is **never** surfaced). Live boot: real factory (Ollama offline) →
  `/log` GET+POST and `/stats` all 200.

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
- 2026-06-20: M4 timeline reconstruction — the retrace interview synthesizes the user's
  ticked rooms + coarse dwell into **contiguous `DwellEntry` intervals** across `[anchor, now]`,
  appends them to `dwell_entries` (`source=retrospective`), then ranks by reading the **whole
  timeline** back through the engine's own windowing. This unifies the hybrid stub's two halves
  through the sacred interface — quick-logged dwells (M5) will feed the same read path for free.
  When no anchor is parsed, retrace starts at a fixed look-back (absolute clock barely matters;
  ranking turns on *which* zones and relative dwell).
- 2026-06-20: M4 rejection pass wiring — `rank_zones` is called with the home zone **excluded**
  (the user just ruled it out), `claimed_zone_id = home` (so adjacency residual lands on home's
  neighbours), and `plausible_zone_ids = dwell zones` so a **transit** space (hallway) can never
  float up via the adjacency floor. Only the **top** suggestion carries a surface hint; each
  zone's reason is grounded in one deterministic fact and worded by `phrase_reason`, with a warm
  fallback when the model is offline *or returns empty*.
- 2026-06-20: M4 metric semantics — DB suggestion ranks **continue after** the first-pass home
  check (home = 1, retrace = 2..K+1), so `finds.was_suggested_rank` and `places_checked`
  (`count_rejected_suggestions + 1`) form one monotone rank-of-actual signal for M5's stats.
- 2026-06-20: M4 learning on confirm — `apply_find` shifts the home prior toward where the item
  actually was (so repeated away finds eventually make that spot lead the first pass — the
  canonical scenario, tested e2e) and bumps failure-mode memory only on an away-from-home find;
  `observe_claim` writes the **silent** memory_log (never surfaced). "None of these" free text
  resolves to a known room or **grows a new zone** so the loop always closes. Append-only:
  `finds`/`memory_log` reject UPDATE+DELETE; a search advances `open→found|expired` and is
  marked `followed_up` so the single next-app-open nudge asks **once**.
- 2026-06-20: M5 quick dwell-log — `/log` writes through the **same** `add_dwell_entry` boundary
  the retrace uses, tagged `source=quicklog`; the find loop reads the whole timeline, so a
  proactively-logged stay feeds ranking with zero extra wiring and the engine never learns it was
  hand-logged (the sacred interface holds). A stay is modelled as ending **now** reaching back by
  a coarse duration (few min / little while / good while) — absolute clock barely matters to
  ranking. Dwell zones only (transit holds nothing); a missing room re-asks, never writes junk.
  `recent_dwell_entries` is display-only (carries `source` for a label) — the engine still reads
  only `read_timeline`.
- 2026-06-20: M5 stats — `/stats` trends the append-only find history; **places-checked** leads
  (the "fewer places before finding" success criterion), with rank-of-actual annotated per find.
  Trend = earlier-half vs recent-half mean, but only committed once there are **≥4 finds** (below
  that it reads "still early" — honest cold-start, aligned with the 5-loss exit criterion).
  `summarize_finds` is a **pure** DB-free roll-up so it's unit-tested without a browser. This page
  is **reporting only** — it never feeds ranking and **never** surfaces the silent memory-trust
  log (pinned by a test).
- 2026-06-20: M5 full-wipe — the only reset is deleting `data/`, kept **out-of-app** by design
  (finds/searches are append-only → no in-app destructive button, consistent with the privacy
  stance). Documented in `CLAUDE.md` (PowerShell + POSIX command) and surfaced as a quiet note on
  the stats page (where the user sees the data held about them); a test pins the instruction so
  the affordance can't silently vanish. Next launch recreates an empty `data/`.
