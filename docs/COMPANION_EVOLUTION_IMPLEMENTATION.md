# Companion Evolution — Interactive Implementation Roadmap

**Repository:** `~/dev/mike/chatbot/python/chatbot`  
**Status:** Task map for planning/review; not an instruction to execute all tasks  
**Requirements authority:** `docs/COMPANION_EVOLUTION_REQUIREMENTS.md`  
**Architecture authority:** `docs/COMPANION_EVOLUTION_ARCHITECTURE.md`  
**Date:** 2026-11-07

## 1. Operating method

We will work through the project **interactively**. Each step is one bounded implementation prompt for Claude Code, AGY or Pi, with explicit file scope, clear success criteria, targeted tests and a short completion report. There is **no** autonomous `/goal` loop, all-night agent run, or orchestration rewrite. One task is reviewed before moving to its dependent task. Parallelize only genuinely independent work that does not race on shared files or database migrations.

The roadmap below is the **complete carry-forward index**, not 20 already-approved change requests. Architecture decisions that depend on repository inspection or product choices remain flagged **DESIGN/APPROVAL**. The user decides what to omit or postpone, and the master requirements file must record that decision by ID.

### Universal task contract

Every coding prompt must include:

1. Relevant requirement IDs, exact task scope and any approved architecture decisions.
2. Existing components to reuse and components that must not be touched.
3. No new frameworks/dependencies or new synchronous model calls without approval.
4. Relevant user/companion/session/room ownership and cancellation semantics.
5. Targeted tests and measurable success criteria, including remote-call count/latency where relevant.
6. Preservation of uncommitted files and unrelated work. No automated commit/push.
7. End report: implementation, files changed, tests, observed limitations, next dependent task.

**Never** implement new features merely to make an evaluation checklist look complete. Performance, scope, and correctness outrank invented abstractions.

## 2. Status key and rough execution budget

`PLANNED` = approved target but task not yet designed; `DESIGN` = needs a product/architecture choice; `READY` = task-specific design approved and can be prompted; `RUNNING` = agent executing; `ACCEPTED` = implementation demonstrated and reviewed; `DEFERRED`/`EXCLUDED` = explicit user decision.

**Estimation only:** approximately 17–25 focused coding prompts across the workstreams, not a promise and not a mandate to fill every slot. Tasks below are **logical work units** and may be combined/split after code-specific inspection. Resist unnecessary planning machinery.

## 3. Phase 0 — Unified conversation lifecycle (**do first**)

**Goal:** standard, streaming and spoken chat share one implementation of companion semantics; transport and cancellation mechanics remain appropriate to each mode. **Do not start Phase 1 until Phase 0 is accepted.**

| Task | Status | Scope / outcome | Requirements | Acceptance evidence |
|---|---|---|---|---|
| **0.1** | **IMPLEMENTED** — design delivered (`COMPANION_PHASE0_REFACTOR_DESIGN.md`) | Map actual duplicated preparation, finalization and post-turn operations from real code. Produce minimal extraction plan; identify exact files and legacy extraction activation policy. **No code change in this step.** | A01, A02, A13 | Per-mode call graph, comparison of effective provider input, persistence, and state processing; approval of smallest extraction approach. |
| **0.2** | **ACCEPTED** — implementation and reported PostgreSQL acceptance verified | Implement single authoritative *shared preparation* path: identity, working history, embedding/retrieval, context/prompt, trace representation. Keep existing JSON/SSE/WS delivery. | A01, A02, A10, A13 | Cross-mode fixture yields equivalent effective prompt/context and remote-call count; type/tests pass; streaming remains incremental. |
| **0.3** | **ACCEPTED** — implementation and reported PostgreSQL acceptance verified | Implement one completion/finalization lifecycle for persistence and eligible post-turn effects; cancellation, retries, scene isolation. Use explicit legacy memory extraction policy; do not introduce a new extraction engine. | A01, A02, A09, A16 | Completed turns produce equivalent effects in all modes; interrupted turns handled correctly; no duplicates, no latency regression. |

### Phase 0 completion gates

- No duplicated companion business rules remaining between `chat.py`, `streaming.py`, and spoken `router.py` for those concerns.
- Provider streaming, model routing, thinking settings, Voice Library, avatar/AVTR event lifecycle and prompt inspection still work.
- One canonical effective context/prompt definition; one completion contract with explicit outcomes.
- Memory side-effect policy consistent across modes, without changing existing data by surprise.
- Embedding batching retained (historical prep 7.63s -> 0.87s) and live TTFT no material regression from historical 2.04s Mercury measured turn.
- Targeted tests on standard, SSE and spoken paths; no speculative full-suite passes or broad refactors.

### Phase 0 status — ACCEPTED (2026-11-07)

Accepted by the owner for integration from `feature/companion-phase0-preparation`. Reported acceptance evidence includes a disposable PostgreSQL 16 + pgvector instance built **only** with `alembic upgrade head`; integration testing on main is recorded separately in the integration report. Details: `COMPANION_PHASE0_REFACTOR_DESIGN.md`, Addenda A–C.

- **Preparation (0.2):** one shared preparation module (`chatbot/api/conversation.py`) and one provider-input path (`prepare_provider_call`). The standard-chat fast path, deferred work and the streaming step loop are preserved by decision.
- **Completion (0.3):** a completed streaming/spoken turn commits its messages and in-turn effects (memories, mood and user state, timeline) as one transaction. Previously those effects were rolled back. Interrupted and failed turns are unchanged or discard their effects as before.
- **Schema defect found during PostgreSQL acceptance:** no Alembic revision created `relationship_timeline_entries`, so on a migrated database every timeline write failed. Fixed by revision **`f687a630a8d1`** (`add_relationship_timeline_entries`, down-revision `e7a4c2d9f1b3`, now the single head). A fresh database built by Alembic alone supports timeline persistence; verified by `tests/test_timeline_migration.py`.
- **Related defect fixed with it:** the standalone `RelationshipTimeline` writers never committed (their sessions closed without a commit), so entries written outside a turn were lost; and without `ON DELETE CASCADE` a session holding a timeline entry could not be deleted. Both are corrected.
- **PostgreSQL results:** 39/39 of the timeline-migration, completion and lifecycle tests pass on that instance (the previously skipped PostgreSQL characterization test now executes). Default-environment targeted run: 215 passed, 9 PostgreSQL-only tests skipped; the wider non-integration suite's failure set is identical to the pre-Phase-0 baseline.
- **Not measured:** the live end-to-end spoken first-text latency (historical 2.04 s) was not re-measured; only local call-count and overhead measurements were taken.
- **Still open (product decisions, not defects):** standard chat's handling of saved mood/user state, room-type prompt delivery, room resolution for sessions linked only through `Room.session_id`, provider-error turn effects, typed SSE disconnect before save. See Design Addenda A.4 and B.4.
- **Known unrelated issues:** `rituals.py` has the same flush-instead-of-commit pattern as the timeline module did (not changed here); `tests/test_schema.py::test_migration_reversible_smoke` is stale (asserts a single migration file); `tests/test_session_delete_cascade.py` needs a seeded built-in voice.

**Important:** The research described regex extraction as skipped in streaming/spoken mode. Phase 0 establishes a single consistent hook; replacement of regex and reliable async memory processing is a **separate Phase 2 deliverable**, not a hidden part of Phase 0.

## 4. Phase 1 — Persistent identity and relationship foundations

**Goal:** retain a minimal configured starting persona and persist evolved companion self and relationship across sessions/restarts, without changing who owns companions or requiring detailed manual configuration.

| Task | Status | Scope | Requirements | Acceptance evidence |
|---|---|---|---|---|
| **1.1** | DESIGN | Approve identity vs relationship scope, lifecycle, schema/migration, existing-state compatibility and deletion behavior. Prefer reuse of existing data fields; new tables only if needed. | P01, P08, A03, A04, A07, A15 | ER diagram + migration and backfill policy approved; user-owned isolation unchanged. |
| **1.2** | PLANNED | Migrate/store durable evolved identity and relationship; load correctly across sessions; keep transient mood separately. | P01, P08, A03, A04, A16 | New session and restart retain evolved state; old saved companions continue to load; deletion leaves no orphans. |
| **1.3** | PLANNED | Hook effective evolved identity/relationship into shared prompt assembly and prompt inspector. Initial seed remains legible, not overwritten. | P01, P08, A11, A13 | Equivalent context across modes; no extra blocking model calls; explicit conflict precedence. |

**Review candidate:** E09 synthetic formative experiences — only after approval. No fabricated observed user history.

## 5. Phase 2 — Durable semantic memory and consolidation

**Goal:** meaningful, reliable memory without per-message synchronous LLM extraction. Reuse `messages`, vector storage and existing background/consolidation components where practical.

| Task | Status | Scope | Requirements | Acceptance evidence |
|---|---|---|---|---|
| **2.1** | DESIGN | Select durable background dispatch mechanism in existing PostgreSQL environment; idempotency, retries, concurrency, cost limits; no new framework. Choose extraction provider/model via explicit approval. | A05, A06, A10, A16 | Restart/retry behavior and failure model defined; bounded budget; no extra TTFT model call. |
| **2.2** | PLANNED | Implement durable completed-turn work dispatch and recovery; all chat modes contribute exactly once. | A01, A06, A09 | Restart loses no committed work; retries do not duplicate memories; cancellation excluded appropriately. |
| **2.3** | PLANNED | Replace regex-only fact extraction with batched semantic extraction and provenance-aware consolidation across fact/episode scopes. | A05, A07, A08, P10 | Realistic corpus precision/recall, corrections, temporal changes, conflicting facts tested; no blocking inference. |
| **2.4** | DESIGN | Memory weighting, indexing/retrieval and selective forgetting: decide query eligibility/compaction vs hard deletion; verify pgvector `<->` vs `<=>` and index configuration explicitly. | P11, A07, A08, A10 | Relevant retrieval beats baseline; no cross-room leaks; long-run growth bounded without unapproved deletion. |

**Do not:** add one LLM call per message, default to synchronous consolidation, or retain the unused regex extractor as a second competing authority.

## 6. Phase 3 — Autobiographical continuity and experience-driven evolution

**Goal:** meaningful experiences cause gradual, explainable, persistent changes; no pseudo-random trait drift masquerading as learning.

| Task | Status | Scope | Requirements | Acceptance evidence |
|---|---|---|---|---|
| **3.1** | PLANNED | Build/reuse narrative episode and shared-experience records with provenance, connections, salience, revisions and durable relationship references. | P07, P10, A07, A08, E05 | Multi-session shared experiences recalled with correct participant/source and timeline. |
| **3.2** | DESIGN | Define compact self-model and identity update contract, anchors/inertia, evidence thresholds and version-aware updates; no separate governance engine. | P01, P04, P07, E01, E06, A16 | Update semantics, source evidence and conflict handling approved. |
| **3.3** | PLANNED | Connect background reflection and evolved opinions/interests to durable identity and prompts. Remove/retire random-drift behavior when replaced. | P04, P07, P09, E05, E06, A11 | Meaningful experiences drive change; simple chatter doesn't cause random personality mutation; cross-session continuity. |

**Review candidates:** P12 contradictions/imperfections; E07 character-specific memory interpretation (factual memory remains grounded); E09 formative fictional experiences.

## 7. Phase 4 — Behavioral personality, initiative and interpretation

**Goal:** different characters make recognizably different choices in the same situation, not merely use different emoji/word counts. **Explicitly includes E02, E03 and E10.**

| Task | Status | Scope | Requirements | Acceptance evidence |
|---|---|---|---|---|
| **4.1** | DESIGN | Define compact behavioral contract for identity -> interpretation -> response inclination -> text, with no default extra model call. Assess effect on streaming TTFT and token cost. | P02, E02, E03, E10, A18 | Decision contract and prompt/metadata shape approved with latency budget. |
| **4.2** | PLANNED | Implement character-aware attention, competing motives, opinion-based responses and principled disagreement in existing generation pipeline. | P02, P04, P13, E02, E03, E06, E10 | Same prompts yield distinguishable decisions across contrasting companions; no boilerplate character drift. |
| **4.3** | PLANNED | Make interests, unfinished subjects, conversational goals and appropriate initiative persistent but not intrusive/scheduled spam. | P05, P09, P16 | Relevant initiative and follow-up without unrelated or repetitive topic changes; privacy/room scope observed. |

**Important:** Avoid a hard-coded response selector and avoid an independent "personality planner" service unless evaluations prove it necessary and the user approves the additional inference/cost.

## 8. Phase 5 — Emotional continuity, social behavior and relationships

| Task | Status | Scope | Requirements | Acceptance evidence |
|---|---|---|---|---|
| **5.1** | PLANNED | Link current simulated mood to stable temperament, evolving relationship and meaningful emotional associations, with appropriate decay across time. | P03, P08, A11 | Mood affects successive replies but doesn't randomly rewrite identity; sessions/restarts behave intentionally. |
| **5.2** | PLANNED | Social interpretation, relationship-specific behavior, shared references/traditions and context-sensitive reactions; reuse same generation context. | P14, E04, P15 | Genuine variation for teasing, disagreement, reunions and familiarity across sessions; no extra blocking model call. |

**Review:** shared traditions P15; conversational maturity A17. No unsolicited ideological or interpersonal-conduct governance additions.

## 9. Phase 6 — Distinctive expression and optional TTS metadata

| Task | Status | Scope | Requirements | Acceptance evidence |
|---|---|---|---|---|
| **6.1** | PLANNED | Make dialogue fingerprint (humor, syntax, rhythm, imperfection and pacing) recognizable and gradually adaptable from seeded character. | P06, P16, A15 | Blind tests distinguish character voice; preserves intelligibility without overconfigured sliders. |
| **6.2** | DESIGN | Define clean dialogue + optional typed per-segment emotion/tone/pace/emphasis metadata, map to TTS *only after verifying engine controls*; text UI receives dialogue only. | P03, P06, A12 | Streaming parser compatibility and clean output; graceful fallback without expressive TTS; no model call added by default. |

**Boundaries:** Qwen and Fish Audio Drama 3 are external renderers of character performance, not sources of identity. Do not refactor AVTR/TTS/voice selection outside an approved adapter integration.

## 10. Phase 7 — Evaluation, integration and performance

Evaluation is **continuous**, not deferred until the end. Phase 7 packages and closes the evidence.

| Task | Status | Scope | Requirements | Acceptance evidence |
|---|---|---|---|---|
| **7.1** | PLANNED | Build/extend three-character, shared-scenario evals (~30 scenarios baseline); test blind recognizability, disagreement, initiative, emotional continuity, factual memory, corrections and cross-session identity. | E08, A14 | Baseline vs improved comparisons; recorded scenario outcomes; no reliance on provider self-report only. |
| **7.2** | PLANNED | Targeted integrated checks for restart/retry, memory cost, throughput, room/user isolation, prompt fidelity and TTFT; remove verified obsolete duplicates. | A09, A10, A13, A14, A16 | Demonstrated behavioral gains without significant regression or uncontrolled background cost. |

## 11. Original idea coverage and disposition guard

**Personality ideas:** `P01`–`P16` are all mapped.  
**Experimental ideas:** `E01`–`E10` are all mapped; `E02`, `E03`, `E10` specifically restored and required.  
**Additional architecture requirements:** `A01`–`A18` mapped to phases and standing constraints.

Before closing any phase, the agent/operator must report:

- Covered IDs and evidence of completion.
- IDs still planned or needing a user decision.
- Any new requirement or deferral with explicit justification.
- Any unplanned new infrastructure/model calls, for approval **before** implementation.

**Do not mark a requirement DONE solely because a table, model or method exists.** The feature must be demonstrably used by actual regular, streaming and spoken conversation where applicable.

## 12. Next action — one bounded Phase 0 assignment

**Task 0.1: code-level consolidation design.** Use the two existing audits as guides, inspect real `chat.py`, `streaming.py`, `turn.py`, `avatar/router.py`, the actual prompt assembly, embeddings, persistence and post-turn steps. Deliver only a short code-level extraction plan, specific functions/files to unify, cancellation/completion semantics, a legacy extraction activation recommendation, targeted test plan, and risk to streaming latency. Confirm whether existing helpers can be reused; **do not modify code**. After that review, issue **one** task 0.2 implementation prompt.

No repo changes are being executed by this roadmap. No commit/push is requested or implied.
