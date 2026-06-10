# wheriz
# Where-Was-I-When: An Ambient Item-Finding System

**Project planning document — pre-implementation**
*Scope: concept, flow, rules, staged plan, costs, and concerns. Technical stack deliberately excluded.*

---

## 1. Concept

A system that helps a person find misplaced items in their home by reasoning over three streams of evidence: where the person physically dwelled (derived from passive WiFi sensing), what the person remembers (treated as valuable but fallible), and what the system has learned about that person's habits over time.

The system never observes items directly. It maintains a probability distribution over likely locations for each tracked item and refines that distribution with every confirmed find. The core product is the reasoning and learning layer; the sensing layer is a replaceable input.

**One-line statement:** "You were on the couch for 40 minutes after you last definitely had your wallet, and the last two times it wasn't at the entrance, it was in the couch — check there first."

---

## 2. Goals

### Primary goals
1. **Answer "where is my X?" with ranked, explained suggestions** that measurably beat the user's unaided search (fewer places checked before finding).
2. **Learn from every find.** Each recovery updates the item's home-location prior and its "failure-mode memory" (where it ends up when it's *not* in the usual place). The system must be visibly smarter on the fifth loss than the first.
3. **Respect and calibrate human memory.** User-reported anchors ("I saw it an hour ago in the bedroom") are honored first, then widened according to a learned, per-user memory error model — never dismissed, never weaponized.
4. **Sensor-agnostic architecture.** The occupancy timeline is a clean interface. Whether dwell data comes from stubbed manual logs, ESP32 nodes, or future sensing-native routers, the reasoning layer is unchanged.

### Secondary goals
- Surface-level spatial knowledge from room photos ("check the counter by the coffee maker," not "check the kitchen").
- Negative-space pruning: zones with no entry events since the last confirmed anchor are eliminated from search.
- Eventual multi-person support via track-then-label identity (deferred; designed-for now).

### Explicit non-goals
- **No item detection.** The system infers from human movement; it never claims to see objects.
- **No vital-sign sensing, no health or medical inference of any kind.**
- **No biometric identification.** Multi-person identity comes from anchor moments (sleep location, phone presence), not body signatures.
- **No guest tracking.** Unrecognized movers remain anonymous tracks and are never profiled.
- **No imaging.** This is presence/dwell sensing, not through-wall vision, and the product must never imply otherwise.

---

## 3. System flow

### 3.1 Onboarding (one-time, ~10 minutes)
1. **Residence description.** User describes rooms and connections in natural language. System builds a room graph (nodes = zones, edges = doorways) with semantic tags (dwell zone vs. transit zone). The walkthrough also defines the **sensing boundary**: the set of zones that constitute the residence. Everything outside this boundary — neighboring units, shared hallways, adjacent apartments — is out of scope by definition.
2. **Room photos (optional but encouraged).** One photo per room yields a surface inventory (counter, entry table, desk, couch) and seeds drop-zone identification. Re-photographing is a lightweight action for when layouts change.
3. **Loss interview (3 questions, 3–5 items).** What do you usually lose? Where does it normally live? Where does it tend to turn up? Answers seed priors at *moderate* confidence — self-reported habits are themselves slightly idealized and will be corrected by real finds.

### 3.2 Ambient operation (continuous, passive)
- The sensing layer logs an **occupancy timeline**: `(zone, enter, exit, dwell duration)`. It knows nothing about items or identity.
- **Anchor-worthy moments** are captured opportunistically: a leaving-home event implies "wallet/keys confirmed on person," refreshing the last-confirmed anchor at zero user effort.

### 3.3 Search interaction (on demand)
1. User asks: "where is my wallet?" — optionally with a memory anchor ("I had it after dinner").
2. System honors the stated anchor and the item's home-location prior first: *"It usually lives at the entrance — checked there?"*
3. On negative evidence ("not there"), probability mass redistributes across the intersection of:
   - zones dwelled in since last-confirmed contact (timeline),
   - the item's plausible-location set,
   - weighted by dwell duration,
   - minus negative-space zones (never entered since the anchor),
   - widened by the user's learned memory error profile (time compression, location substitution).
4. Output: 2–4 ranked candidates, each with a one-line reason, each presented as a tappable option.

### 3.4 Find-loop closure (the learning step)
- **One-tap outcome:** tapping the place it was found is the confirmation. Zero extra effort.
- **"None of these" + free text** for surprising finds — the highest-value training data.
- **Implicit closure:** a leaving-home event shortly after a search implies recovery (window closes; weak credit to unrejected top suggestion).
- **Single gentle follow-up** if the search goes silent — once, soon, never nagging.
- Every confirmed find updates: the home-location prior (decaying average — recent finds weigh more), the failure-mode memory (P(location | not at home spot)), and the memory-trust model (claimed anchor vs. actual outcome).
- The system acknowledges learning visibly ("got it — couch goes on the wallet's list") so users see a return on reporting.

---

## 4. Rules (non-negotiable design principles)

1. **Suggestions, never assertions.** All output is probabilistic and framed as such. The system reasons from dwell + plausibility, not observation.
2. **The user's account comes first.** Stated anchors are always searched before the system widens beyond them. Widening is framed as normal brain behavior ("things get set down on autopilot"), never as distrust.
3. **The memory-trust model is silent.** It adapts quietly and is never surfaced as a score or report. If the user base ever includes people with memory impairment — a plausible audience — this becomes sensitive territory; the rule exists from day one.
4. **Learning requires confidence.** Habit profiles only consume dwell data above an identity-confidence threshold. Ambiguous intervals (e.g., after two tracks merge) are discarded rather than risk polluting a profile.
5. **Privacy is structural, not a setting.**
   - Per-person movement timelines are private to that person by default.
   - Identification is opt-in per resident.
   - Guests are never labeled or logged as individuals.
6. **The sensing boundary is hard.** Only zones inside the residence (as defined at onboarding via the walkthrough, descriptions, and photos) are tracked. Signal perturbations attributable to movement outside the boundary — neighbors through shared walls, people in apartment hallways, units above or below — are filtered and discarded, never logged. This is both a correctness rule (out-of-boundary motion would poison the timeline with phantom dwell events) and an ethical one (people in other units never consented and must never be sensed into the system's data). Apartment-style housing is a first-class deployment target, not an edge case.
7. **Local storage is the default — and data is tiered by sensitivity.** All system data (occupancy timelines, habit profiles, memory-trust models, find histories, room photos) is stored locally on the user's own hardware. If a cloud component is ever added — hosted model, hosted storage, or both — data crosses the boundary according to an explicit tiering:
   - **May leave (innocuous tier):** the minimal output of a query — ranked likely locations, generic item names, a short explanation. The "answer," not the evidence.
   - **Never leaves (identifying tier):** raw occupancy timelines, dwell histories, per-person behavioral patterns, habit profiles, memory-reliability models, sleep/wake patterns, and room photos. These constitute a detailed behavioral portrait and remain in the secured local environment regardless of architecture.
   The principle: anything that describes *where the wallet probably is* may travel; anything that describes *who you are and how you live* may not.
8. **Local-first inference.** All LLM-dependent functions (onboarding parsing, photo analysis, query explanation, anchor interpretation) run against a local model on the user's own hardware by default. Hosting the model and/or storage in the cloud is a future, opt-in expansion — and if adopted, the data-tiering rule above still governs: only innocuous-tier outputs cross the boundary; identifying-tier data stays local.
9. **Honest capability claims.** RF resolves to zone; photos resolve to surface. The UX may combine them ("kitchen → likely the counter") but must never imply the system can see surfaces or objects.
10. **The timeline interface is sacred.** Nothing above it may depend on how dwell data is produced.

---

## 5. Staged plan

### Stage 0 — Reasoning engine with stubbed sensing
**Build:** Room graph + item model + inference engine + find-loop UX + learning updates, with the occupancy timeline supplied by manual logging or a phone-location proxy. Onboarding flow including the loss interview and photo parsing.
**Prove:** The full loop — "not at the entrance → reasons to couch → user confirms → suggests couch faster next time" — works as pure software and beats unaided search.
**Exit criteria:** Five or more real loss events resolved on yourself with the system's suggestions; visible improvement in ranking by the later events.

### Stage 1 — Real sensing, single occupant
**Build:** Replace the stub with live dwell-zone data from 2–4 sensing nodes. Per-zone calibration routine, including a **boundary-rejection pass** for apartment environments (out-of-boundary motion from neighbors or shared hallways is identified and discarded, guided by the residence boundary defined at onboarding). Negative-space pruning goes live (it's a free filter once real entry/exit events exist).
**Prove:** Zone-level dwell detection is reliable enough in *your* home that the timeline is trustworthy without manual correction.
**Known battle:** Environment brittleness. Furniture moves, calibration drifts. Budget real time for re-calibration tooling — this is the stage where most WiFi-sensing projects stall.
**Exit criteria:** A week of unattended operation producing a timeline you'd trust for inference.

### Stage 2 — Lived-in refinement
**Build:** Anchor-moment capture (leaving-home pings), the memory error model accumulating real claimed-vs-actual data, failure-mode memories deepening, photo-derived surface suggestions in the output.
**Prove:** The system is something you actually reach for when you lose things, not a demo.
**Exit criteria:** It has genuinely found something for you that you wouldn't have checked first.

### Stage 3 — Multi-person (deferred, designed-for)
**Build:** Anonymous track maintenance → anchor-based labeling (sleep location as the strong anchor; phone presence as the workhorse re-anchor; habitual seats as soft re-anchors) → identity as a decaying confidence value → ambiguity-aware learning. Per-person priors ("Sam leaves keys on the dresser").
**Known battle:** Track fragmentation and identity swaps when people cross paths or share a couch. Swaps *persist* until the next hard anchor — hence confidence decay and the discard rule.
**Not built:** gait/body-shape biometrics. Height is a weak tiebreaker at best on this class of hardware; treat as a bonus if it falls out of the data, never load-bearing.

### Stage 4 — Scaling questions (decision point, not a build stage)
Only relevant if the project outgrows personal use. The sensing layer's consumer path runs through chipset vendors, ISP router partnerships, and the maturing WiFi-sensing standardization effort (802.11bf-class capability) — not through third-party software on arbitrary routers, which is not viable today. The defensible asset is the reasoning layer: memory-trust modeling, failure-mode learning, and the find-loop UX, all of which are sensor-agnostic by design. A scaling decision triggers a full privacy/legal review (see concerns).

---

## 6. Costs

### Stage 0 — effectively software-only
| Item | Cost |
|---|---|
| Hardware | $0 (phone + existing computer; local model runs on owned GPU) |
| LLM inference | $0 — local model by default; cloud APIs are a future opt-in expansion (~$5–20/month if ever adopted) |
| **Stage total** | **≈ $0** |

### Stage 1 — sensing hardware
| Item | Cost |
|---|---|
| ESP32-class sensing nodes, 3–5 units | $5–12 each → **$15–60** |
| USB power adapters / cables (often spares on hand) | $0–25 |
| Optional spare units for placement experiments | $10–25 |
| A receiving machine that's always on (existing laptop/Pi suffices) | $0 (or ~$50–80 if buying a dedicated single-board computer) |
| **Stage total** | **≈ $15–110 one-time** |

### Stage 2 — no new hardware
Possibly one additional node if a zone proves dead; inference remains local and free. **≈ $0–15.**

### Stage 3 — multi-person
One or two additional nodes for coverage in shared spaces; phone-presence detection is free (network-level). **≈ $10–30.**

### Hidden costs (worth pricing in honestly)
- **Calibration time.** Stage 1's real cost is evenings spent on node placement and re-calibration after the living room gets rearranged. This dwarfs the dollar cost.
- **Always-on compute.** A continuously listening receiver draws a few watts — trivial in dollars, but it means a machine is always running in the home.
- **Cooperation of housemates** (Stage 3): consent conversations are a real cost and a hard prerequisite, not a formality.
- **Local model overhead.** Running inference locally means the model shares the GPU with everything else on that machine, and quality/latency are bounded by what fits in local VRAM. Acceptable for this workload (parsing, short explanations), but it's a constraint to design prompts around — and the reason a cloud expansion path is kept open rather than ruled out.

**Total to a fully working single-occupant system: roughly $15–150 one-time, with zero recurring spend under the local-first default.** This is deliberately a software-risk project, not a hardware-risk one.

---

## 7. Concerns and risks, by stage

### Before any code (now)
- **Scope discipline.** The adjacent capabilities of WiFi sensing (vitals, identification, through-wall awareness of others) are explicitly out. The non-goals section exists to be pointed at when feature creep arrives — including your own.
- **Attribution honesty.** The single biggest conceptual risk is the system (or its creator) forgetting that timeline-to-item attribution is inference, not fact. Bake "suggestions, never assertions" into the output layer from the first prototype.

### Stage 0 concerns
- **Garbage priors feel bad fast.** With no find history, the system leans on onboarding answers and commonsense. Set expectations in the UX ("I'll get sharper as we find things together") so early mediocrity reads as cold start, not failure.
- **Find-loop friction.** If confirming a find takes more than one tap, the learning loop dies and the project plateaus permanently at one-shot guessing. This is the highest-leverage UX problem; treat it as such.

### Stage 1 concerns
- **Brittleness is the boss fight.** Models and calibrations tuned to one room arrangement degrade when the environment changes. Plan for recalibration as a routine operation, not an exception.
- **Boundary enforcement in shared buildings.** WiFi doesn't stop at your walls — in apartment-style housing, a neighbor walking on the other side of a shared wall or someone passing in the corridor perturbs the signal too. Calibration must include a boundary-rejection step: characterize what out-of-boundary motion looks like (typically weaker, spatially inconsistent with the room graph, or localized to perimeter walls) and discard it before it reaches the timeline. Validate this explicitly — invite someone to walk the hallway and confirm the system logs nothing. Until boundary rejection is verified, the timeline can't be trusted in an apartment.
- **Timeline trust.** If dwell data is wrong even 15% of the time, inference quality collapses quietly — wrong suggestions with confident reasoning. Build a way to spot-check the timeline against ground truth before trusting it.
- **Household RF coexistence.** Sensing traffic shares spectrum with the home's actual WiFi. Keep node traffic modest; nobody should notice the system exists.

### Stage 2 concerns
- **Memory-trust sensitivity.** The error model must adapt invisibly. Any surfacing of "your memory was wrong N times" is a betrayal of the design's spirit — and if the eventual audience includes people with cognitive decline, it becomes genuinely harmful. Quiet adaptation, warm framing, always.
- **Over-personalization creep.** The system now holds a detailed behavioral portrait: where you sit, when you leave, how reliable your memory is. The local-storage default and identifying-tier classification cover where it lives; an easy full-wipe covers the user's right to make it stop existing. Build both.

### Stage 3 concerns
- **Consent is per-person and revocable.** Every labeled resident opts in themselves; opting out deletes their profile and stops labeling, not just hides it.
- **Identity swaps poison learning.** A persisted swap mislabels hours of behavior. The confidence-decay + discard rules are the mitigation; verify they actually fire in crossing-path scenarios before trusting multi-person habit data.
- **The guest problem.** Visitors are sensed whether or not they're labeled. The rule (anonymous tracks, never profiled, not retained) must be implemented, not just stated.
- **Relationship misuse.** A queryable "where has this person been" capability is exactly the artifact of concern in controlling-partner dynamics. Per-person timeline privacy is the structural defense; no resident can query another's history, full stop.

### Stage 4 (scaling) concerns
- **Legal exposure.** Passive in-home sensing of individuals intersects wiretap/surveillance statutes, biometric privacy laws, and consumer-protection rules that vary sharply by jurisdiction. A scaled product requires legal review before a single external user.
- **Data gravity.** The local-first default (local model, local storage) is a values decision as much as a cost one. A future cloud expansion — hosted model, hosted storage, or both — is permitted only under the data-tiering rule: innocuous-tier query outputs may travel; identifying-tier behavioral data (timelines, habit profiles, memory models, photos) never does. The moment that line blurs, the data becomes subpoenable, breachable, and monetizable — revisit deliberately or not at all.
- **Capability drift.** A scaled platform will face pressure (commercial or otherwise) to add identification, guest analytics, or health inference. The non-goals list should survive contact with growth.

---

## 8. Success criteria, restated simply

1. It finds things faster than you would alone.
2. It is visibly smarter on the fifth loss than the first.
3. It never makes the user feel surveilled, doubted, or watched — including the people who didn't ask for it to exist.
4. The reasoning layer would survive its sensing layer being swapped out tomorrow.

When all four hold for a single occupant in one home, the project has earned its next stage.
