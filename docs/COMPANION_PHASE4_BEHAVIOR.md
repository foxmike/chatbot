# Companion Evolution — Phase 4.1 Behavioral Personality Contract

**Status:** DESIGN, awaiting approval of §7. No production code. Implemented by Task 4.2.
**Requirements:** P02, P04, E02, E03, E10, A18 (with A10, A11, A13 as constraints). Baseline: `main` at `cd264ad`.

## 1. Principle

Identity → interpretation → inclination → dialogue is a **responsibility model, not four stages**. Deterministic code only *selects and compacts inputs the system already holds*; the existing conversation model, in its one existing generation, does the noticing, weighing and choosing. There is no planner, no second pass, no structured-output step, no response selector and no new storage. Characters differ because their *state* differs, never because code prefers a temperament.

## 2. Existing interfaces reused

| Need | Reuse |
|---|---|
| One place all modes share | `CompileContextStep.run` (`chatbot/core/turn.py`): standard (fast and full), SSE and spoken all pass through it (Phase 1.3 / 3.3) |
| Prompt assembly | `compose_prompt_layers` / `PromptLayerName` / `PROMPT_LAYER_ORDER` / `PROMPT_LAYER_TITLES` (`chatbot/core/prompt_layers.py`) |
| Character state | `ctx.metadata`: `companion_identity` (effective traits), `companion_relationship`, `companion_self_model`, configured `character_profile` (favourite topics) — loaded by `load_companion_inputs`, no new query |
| Developed views | `held_items()`, subject normalisation and stemming in `chatbot/soul/self_model.py` (tentative items are never used) |
| Relevant experiences | `ctx.retrieved_memories` from `RetrieveStep` (shared experiences carry `extraction_metadata.kind` and `experience.title`) |
| Situation | `ctx.intent_classification` (already computed at the top of the step), mood and mode already in `Response guidance` |
| Exclusions | roleplay mode and `_roleplay_scene_prompt` / `scene_memory_isolation` |
| Inspection | `compile_context` step metadata (`identity_sources`) and `trace.prompt` (= exact provider input) |
| Latency evidence | Phase 0 trace timings (`provider_http_start`, `provider_first_event`, `provider_first_visible`) |

Not touched: provider selection and `ProviderConfig`, mode contracts, memory extraction, the self-model, retrieval, persistence, the persona cache (companion sessions already bypass it).

## 3. Behavioral context contract

One new pure function, `build_behavior_context(...) -> (text, metadata)` in `chatbot/soul/behavior.py`: no I/O, no model call, no randomness, no clock. Same inputs give identical text.

**Inputs (all read-only, all existing):** effective traits; relationship settings; held self-model items; configured favourite topics; retrieved experiences and facts; the user message; intent.

**Output:** one block, at most **1,200 characters** (about 300 tokens), as a new prompt layer `character_behavior` ordered after `mood_state` and before `memory`:

```
CHARACTER BEHAVIOR:
What pulls on you: curiosity 0.90 (high), agreeableness 0.30 (low), directness 0.80 (relationship)
Noticing in this message:
- your view on <subject>: <statement>
- your interest in <subject>
- shared history: <experience narrative>
<fixed stance paragraph>
```

**3.1 Competing motivations (E02).** Not enumerated and not resolved by code. *Pulls* are the character's own configured or developed leanings, shown neutrally as `name value (high|low)`:
- effective trait sliders with |value − 0.5| ≥ 0.2, and relationship `directness`, `warmth`, `initiative_level` that differ from `RelationshipConfig.defaults()` by ≥ 0.2;
- at most 4, ordered by distance from neutral, ties alphabetical;
- both poles are rendered identically, with no adjectives such as "kind" or "harsh".

A conflict (candour against tact, curiosity against caution, independence against cooperation, enthusiasm against restraint) exists when two pulls, or a pull and a noticed view, point different ways in *this* message. The model weighs them using the persona text already in the prompt, which carries any named values. No pole-to-motivation table exists; those examples appear only in this document.

**3.2 Selective attention (E03).** *Noticing* lists up to 3 candidates, at most 2 per source, drawn only from this companion's own state: held opinions, interests, preferences and self-understanding; configured favourite topics; retrieved experiences and facts. A candidate qualifies when it shares content stems with the message (at least 1 for a subject or topic, at least 2 for narrative text), using the same stemmer as the self-model. Order is score, then a fixed source order (opinion, interest, preference, experience, topic, fact). Two companions see different aspects of one message because their pools differ, not because of rules.

**3.3 Private interpretation (E10).** Instruction only, with no field, schema, output, log or persistence. The stance paragraph is **byte-identical for every companion, turn and mode**:

> Before replying, privately decide what in this message matters to you, which of your pulls point different ways here, and what you want to do about it. Let that decide what you do: you may agree or disagree, ask, share a view, show curiosity, challenge an assumption, joke, or change the emphasis, or none of these. Do not describe, label or explain this thinking; it shows only in what you say.

The choices are listed symmetrically; nothing prefers agreement, warmth or any temperament, and nothing is a canned response. No reasoning mode is requested or changed (`thinking` is untouched), and extraction still sees only the user and assistant text.

**3.4 Eligibility.** Included for companion-bound sessions in standard (fast and full path), SSE and spoken chat. Omitted for companion-less sessions, roleplay mode and scene rooms (the role defines behaviour there; consistent with Phase 3 exclusions) and on any error (logged; the turn proceeds, as for other prompt sections).

**3.5 Bounds and degradation.** If the block exceeds 1,200 characters, drop Noticing items from lowest score, then Pulls from smallest deviation; the stance paragraph is never dropped. With no pulls and nothing noticed, the block is the stance paragraph alone (about 400 characters).

## 4. Generation integration

1. `CompileContextStep.run` calls `build_behavior_context` after intent classification, and passes the text as layer `character_behavior` in the existing `compose_prompt_layers` call. Add the enum value, its order slot and its title; nothing else in layer composition changes.
2. The conversation provider is called once, exactly as today. No change to `ProviderConfig`, temperature or `max_tokens`.
3. All three modes share the step, so equivalent state gives the same block (verified as in Phase 1.3/3.3).
4. The block sits in the system message with persona and memory. It is never stored, never sent to extraction, and never shown as dialogue.

## 5. Prompt Inspector

`trace.prompt` shows the block verbatim (A13). The `compile_context` step metadata gains:

```json
"behavior": {"version": 1, "chars": 712, "est_tokens": 178,
  "pulls": [{"source": "trait|relationship", "name": "curiosity", "value": 0.9}],
  "noticed": [{"source": "opinion|interest|preference|self_understanding|topic|experience|fact",
               "ref": "<self-model key or memory id>", "matched": ["stem"]}],
  "dropped": 0, "omitted": null}
```

`omitted` is `roleplay`, `scene`, `no_companion` or `error`. There is no interpretation payload because none exists. A small inspector panel is a 4.2 frontend item.

## 6. Token and latency budget

- **Tokens:** at most +300 input tokens per turn (typical 180–250); nothing added to output.
- **Compute:** no I/O, no query; at most 2 ms p95 for at most about 60 candidates.
- **Calls:** zero additional model, embedding or database requests.
- **First-text latency:** the only cost is extra prefill. Task 4.2 measures baseline against behavior-on with the Phase 0 trace timings: at least 30 turns each, same seeded state, fast path, full path and spoken, on the configured conversation model. Proposed gate: median first-visible increase ≤ 50 ms and p95 ≤ 100 ms. If exceeded, the response is to shrink the block (cap 800 characters), not to add a planner.

## 7. Acceptance criteria (exact)

Deterministic tests (4.2, mocked provider recording its inputs):
1. One conversation generation per turn in every mode; `ProviderConfig` and extraction inputs identical to baseline.
2. The block is present and byte-identical for equivalent state in standard (fast and full), SSE and spoken; absent for roleplay mode, scene rooms and companion-less sessions.
3. The same message to two companions with different traits, relationship settings and self-models gives different Pulls and Noticing, and every item comes from that companion's own state (no cross-companion, cross-user or cross-room items).
4. The stance paragraph is byte-identical across companions; flipping a companion's agreeableness between 0.2 and 0.9 changes only the Pulls line.
5. Output is ≤ 1,200 characters for adversarial state (many long items); truncation follows §3.5; same inputs give identical text.
6. Tentative self-model items never appear; configured anchors always appear unmodified in Pulls when they qualify.
7. Metadata matches §5 and every `ref` resolves to a real item.
8. An error in the builder omits the block and the turn still completes.

Owner-run evaluation in 4.2, with the real conversation model and no mocks: at least 12 situations × at least 3 contrasting companions; reviewers can tell the companions' decisions apart in at least 70% of pairs; the block's labels or phrases appear in 0 replies; across the set a low-agreeableness companion disagrees or challenges more often than a high-agreeableness one. The latency gate of §6 is reported with it.

## 8. Decisions requiring approval

| # | Decision | Recommendation |
|---|---|---|
| D1 | New prompt layer `character_behavior` after `mood_state` rather than inside the identity section | **Approve**: it is per-turn content and keeps identity text cacheable and unchanged |
| D2 | Include the block on the fast path and for greetings | **Approve**: consistency across modes; cost is bounded, and Noticing is simply empty |
| D3 | Budgets: +300 tokens, first-text median +50 ms, p95 +100 ms | Approve as starting values; they can only be validated by the 4.2 measurement |
| D4 | Attention matching is lexical, so paraphrase can be missed; semantic matching needs an embedding call that is not approved | **Approve lexical** for 4.2 and revisit with evaluation data |
| D5 | Omit the block for roleplay mode and scene rooms | **Approve** |
| D6 | Leakage of private interpretation into replies is handled by instruction only, not by a post-generation detector | **Approve**; revisit only if the evaluation shows any leakage |
| D7 | If evaluation shows too little distinguishability, any planner, second pass or extra call needs a separate, explicit cost approval | Confirm |
