# Where-Was-I-When — MVP (Stage 0) Specification

> **This file is the design reference (the *what* and *why*).**
> Live progress lives in [MVP-PLAN.md](MVP-PLAN.md) (the *where we are*).
> Full product concept: [item-finder-project-plan.md](item-finder-project-plan.md).

## Context

[item-finder-project-plan.md](item-finder-project-plan.md) describes an ambient item-finding system that reasons over occupancy dwell data, fallible human memory, and learned habits to answer "where is my X?" with ranked, explained suggestions. The MVP is **Stage 0 in full**: the complete reasoning engine, onboarding (NL room description, room photos, loss interview), find-loop UX, and learning updates — with the occupancy timeline **stubbed** (no sensing hardware). Exit criterion: five real loss events resolved with visible ranking improvement.

## Decisions (from grilling interview)

| Decision | Choice |
|---|---|
| Scope | Full Stage 0 (incl. LLM onboarding + photo surface extraction) |
| Platform | Desktop-only for now — localhost web UI (phone-reachable later by binding to LAN, zero rework) |
| Timeline stub | **Hybrid**: retrospective reconstruction at query time (default) + optional quick dwell-log |
| LLM | Ollama, local: 8–14B text model + vision model. **GPU present, Ollama not yet installed** — setup is a milestone |
| Stack | Python 3.11+, FastAPI, simple server-rendered web UI |
| Inference | Deterministic scoring in Python; LLM only parses language in and phrases reasons out — never ranks |
| Location model | Probability at **zone** level; surfaces (from photos/manual) decorate suggestions, finds record the surface |
| Memory model | Fixed widening defaults now; claimed-vs-actual logged from day one; learned fitting deferred |
| Find loop | One-tap confirm + "none of these"+free text + single next-app-open follow-up. Implicit closure deferred (needs sensing) |
| Persistence | SQLite, single file in `data/`; one-command full wipe |
| Metrics | Instrumented from day one: rank-of-actual-location, places-checked, trend view |
| Repo | `git init` in `C:\Users\dsolg\Downloads\wheriz`; `data/` gitignored from first commit |

Note: `C:\Users\dsolg\Downloads\CLAUDE.md` belongs to a different project (cognitive-engine) — ignore it. This project gets its own CLAUDE.md.

## Architecture

```
wheriz/
├── item-finder-project-plan.md   # existing doc
├── CLAUDE.md                      # project conventions (privacy rules, run/test commands)
├── MVP.md                         # this file — design reference
├── MVP-PLAN.md                    # living progress tracker
├── pyproject.toml
├── .gitignore                     # data/ excluded entirely
├── src/wwiw/
│   ├── db.py                      # SQLite schema + access (append-only finds/searches)
│   ├── engine/
│   │   ├── types.py               # domain dataclasses (pure, no I/O)
│   │   ├── scoring.py             # pure functions: rank zones given evidence
│   │   ├── learning.py            # prior/failure-mode updates on confirmed finds
│   │   └── memory.py              # anchor widening (fixed defaults), claimed-vs-actual log
│   ├── llm/
│   │   ├── client.py              # Ollama HTTP client (text + vision), call logging
│   │   └── prompts/               # one file per task, never inline
│   ├── web/                       # FastAPI app, Jinja templates, minimal JS
│   └── main.py                    # entry point: uvicorn on localhost
├── tests/                         # pytest, mirrors src; engine tested w/ synthetic fixtures
└── data/                          # gitignored: wwiw.sqlite, photos/, llm_logs/
```

### Data model (SQLite)

- `zones` (id, name, kind: dwell|transit), `zone_edges` (doorways), `surfaces` (zone_id, name, source: photo|manual)
- `items` (id, name, home_zone_id, home_surface_id?)
- `priors` (item_id, zone_id, weight) — home-location prior, decaying average
- `failure_modes` (item_id, zone_id, count, decayed_weight) — P(zone | not at home spot)
- `dwell_entries` (zone_id, enter, exit, source: retrospective|quicklog)
- `searches` (item_id, anchor_claim_text, anchor_time, status: open|found|expired, followed_up)
- `suggestions` (search_id, zone_id, surface_id?, rank, reason, rejected)
- `finds` (search_id, zone_id, surface_id?, was_suggested_rank, places_checked) — **append-only**
- `memory_log` (search_id, claimed_anchor, actual_outcome) — claimed-vs-actual, silent

### Scoring (deterministic, pure functions)

1. **First pass**: honor stated anchor + home prior — "usually lives at X, checked there?"
2. **On rejection**: `score(zone) ∝ failure_mode_weight(item, zone) × dwell_weight(zone, since anchor)` over zones in the retrospective timeline ∩ plausible set, **minus** zones never entered since anchor (negative space), with anchor window widened by fixed defaults (time ±50%, small residual mass to zones adjacent to claimed zone).
3. Output 2–4 candidates, each with a reason template the LLM phrases warmly. Top suggestion names the zone's most-likely surface as a hint.
4. **On confirmed find**: exponential-decay update to home prior (recent finds weigh more), increment failure-mode weight if found away from home spot, append memory_log row, show "got it — couch goes on the wallet's list."

### LLM tasks (Ollama; all output user-confirmable before commit)

- Parse NL residence description → room graph JSON (user reviews/edits before save)
- Parse loss-interview answers → items + moderate-confidence priors
- Photo → surface inventory (vision model; user prunes the list)
- Parse search query + memory anchor ("I had it after dinner" → item + time)
- Phrase one-line suggestion reasons (template-grounded — LLM words it, math decides it)

Every call logged (inputs/outputs/model) to `data/llm_logs/`.

## Milestones (summary; status tracked in MVP-PLAN.md)

**M0 — Environment & repo.** `git init`, `.gitignore` (data/, llm logs), pyproject, CLAUDE.md with privacy rules. Install Ollama; pull text model (`qwen3:8b` class) + vision model (`qwen2.5vl:7b` class — adjust to VRAM); smoke-test both.

**M1 — Schema + engine core.** SQLite schema; `scoring.py`/`learning.py`/`memory.py` as pure functions with pytest coverage on synthetic fixtures. Key test: a scripted sequence of 5 synthetic losses where rank-of-actual demonstrably improves — the exit criterion as a unit test.

**M2 — LLM edge layer.** Ollama client, prompt files, the five parse/phrase tasks, call logging. Tests with recorded fixtures (engine tests never depend on live LLM).

**M3 — Onboarding wizard (web).** Rooms (NL → graph → user confirms) → photos (vision → surface list → user prunes) → loss interview (3 questions, 3–5 items → seeded priors).

**M4 — Search + find loop (web).** Query box with anchor field → retrospective timeline interview ("where have you been since?") → ranked tappable suggestions → one-tap confirm / "none of these"+free text → learning update + visible acknowledgment. Next-app-open follow-up for open searches (asks once, then expires them).

**M5 — Quick dwell-log + stats view.** Minimal dwell quick-entry page (hybrid stub's second half). Stats page: rank-of-actual and places-checked over time. Full-wipe command (delete `data/`).

**M6 — Dogfood.** Use it for real losses; fix friction. Done when 5 real loss events are resolved and the stats view shows improvement.

## Non-negotiables carried from the doc

- Suggestions, never assertions — probabilistic framing in every output
- Memory-trust data is silent — never surfaced as a score
- All inference local; nothing leaves the machine; `data/` never enters git
- The timeline interface is sacred: engine consumes `(zone, enter, exit, dwell)` and must not know whether it came from retrospective entry, quick-log, or (later) sensors

## Verification

- `pytest` — engine invariants: scores normalized, negative-space zones never suggested, finds append-only, ranking-improves-over-synthetic-sequence test
- Scripted demo: seed synthetic onboarding, run the doc's canonical scenario ("not at entrance → reasons to couch → confirm → couch ranked first next time") end-to-end via the API
- Manual: full onboarding on the real residence, then live dogfooding; stats view is the ongoing measure
- Privacy check: `git status` clean of `data/` before every commit
