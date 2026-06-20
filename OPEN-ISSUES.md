# Open Issues

Running list of unresolved decisions and deferred work that doesn't belong in
[MVP-PLAN.md](MVP-PLAN.md) (the *what's done / what's next* tracker) or
[MVP.md](MVP.md) (the static design spec). Each entry: what, why it's open, and what
would close it. Resolved items move to the plan's notes log and are deleted here.

---

## 1. Model selection (Ollama text + vision) — OPEN

**What.** Which local Ollama models the app actually runs. The code defaults live in
`OllamaConfig` (`src/wwiw/llm/client.py`): text `qwen3:8b`, vision `qwen2.5vl:7b`. These
are placeholders from the M0 spec, not a confirmed choice.

**Why it's open.**
- The Ollama binary is installed and (as of 2026-06-20) the **server is running** —
  `GET /api/tags` answers — but `models: []`: **no models are pulled yet**. So the next
  concrete action is simply `ollama pull` of the chosen text + vision models.
- Note the nuance, now handled in code: `is_available()` only confirms the *server*
  answered, not that a usable model exists. Onboarding catches `LLMError` and degrades to
  manual entry, so a missing model no longer 500s — but live parsing still needs a pull.
- The target machine has an RTX 5060 Ti (16 GB VRAM). Both models must co-resident or
  swap cleanly within that budget; the 8B text + 7B vision pair should fit, but this is
  unverified against real latency on the find-loop and onboarding flows.
- Model quality on the five tasks (residence parse, loss interview, surface extraction,
  query parse, reason phrasing) hasn't been spot-checked; a different size/quant may word
  reasons better or parse JSON more reliably.

**What would close it.**
1. ~~`ollama serve` running~~ (done); `ollama pull` the chosen text + vision models.
2. Confirm both load within 16 GB and respond at acceptable latency.
3. Spot-check each of the five tasks against real (non-synthetic) onboarding input.
4. Record the final model ids in `OllamaConfig` defaults and note the choice in the
   plan's decisions log; delete this issue.

**Candidates to weigh** (adjust to VRAM/quant): text — `qwen3:8b`, `llama3.1:8b`,
`mistral-nemo:12b`; vision — `qwen2.5vl:7b`, `llama3.2-vision:11b`, `minicpm-v`.

**Not a blocker for code.** Every LLM path is gated by `OllamaClient.is_available()`, and
the whole test suite injects a fake client — nothing depends on a live model to build or
test. This issue blocks only live dogfooding (M3 onward).
