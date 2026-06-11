# Where-Was-I-When (wwiw)

An ambient item-finding system. Stage 0 MVP: full reasoning engine, onboarding, find-loop UX, and learning — with the occupancy timeline stubbed (retrospective reconstruction + optional quick dwell-log). Full concept: `item-finder-project-plan.md`. Implementation plan decisions: see the approved MVP plan (deterministic scoring, LLM at edges only, zones score / surfaces decorate).

## Stack

- Python 3.11+, FastAPI + Jinja2 served on localhost (desktop-only for now; LAN/phone later)
- SQLite in `data/wwiw.sqlite`
- Ollama (local) for all LLM tasks: text model for parsing/phrasing, vision model for room photos
- pytest; engine tests never depend on a live LLM

## Architecture rules (non-negotiable)

- **Deterministic core.** Ranking and learning are pure Python in `src/wwiw/engine/`. The LLM never ranks — it only parses user language in and phrases reasons out.
- **The timeline interface is sacred.** The engine consumes `(zone, enter, exit, dwell)` and must not know how the data was produced (retrospective, quick-log, or future sensors).
- **Suggestions, never assertions.** All output is probabilistic framing. RF-class evidence resolves to zone; photos resolve to surface; never imply the system sees objects.
- **Memory-trust data is silent.** Claimed-vs-actual is logged but never surfaced as a score or report. Anchor widening is framed warmly, never as distrust.
- **Finds and searches are append-only.**
- Prompt templates live in `src/wwiw/llm/prompts/`, one file per task, never inline in business logic. Every LLM call is logged to `data/llm_logs/`.

## Privacy (structural, not a setting)

- **`data/` is gitignored in its entirety** — timelines, find history, photos, memory logs, LLM logs are deeply personal and never enter version control. Check `git status` before every commit.
- Test fixtures use synthetic data only, never real residence/find content.
- Everything runs and stays local. No cloud calls.
- A full wipe (delete `data/`) must always work.

## Commands

- Run app: `python -m wwiw.main` (serves http://127.0.0.1:8741)
- Tests: `python -m pytest`
- Install: `pip install -e .[dev]`

## Conventions

- Type hints on public functions; dataclasses for domain types
- Pure functions for scoring/learning math; side effects only at the DB boundary
- One logical change per commit; run tests before committing
- No attribution trailers in commits
