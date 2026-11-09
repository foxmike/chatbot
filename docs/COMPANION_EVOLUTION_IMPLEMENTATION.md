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

## 4. Phase 1 — Persistent identity and relationship foundations — ACCEPTED

**Goal:** retain a minimal configured starting persona and persist evolved companion self and relationship across sessions/restarts, without changing who owns companions or requiring detailed manual configuration.

| Task | Status | Scope | Requirements | Acceptance evidence |
|---|---|---|---|---|
| **1.1** | **ACCEPTED** — architecture and decisions complete; recorded in `COMPANION_PHASE1_DESIGN.md` | Approve identity vs relationship scope, lifecycle, schema/migration, existing-state compatibility and deletion behavior. Prefer reuse of existing data fields; new tables only if needed. | P01, P08, A03, A04, A07, A15 | ER diagram + migration and backfill policy approved; user-owned isolation unchanged. |
| **1.2** | **ACCEPTED** — implementation and successful acceptance evidence recorded below | Migrate/store durable evolved identity and relationship; load correctly across sessions; keep transient mood separately. | P01, P08, A03, A04, A16 | New session and restart retain evolved state; old saved companions continue to load; deletion leaves no orphans. |
| **1.3** | **ACCEPTED** — implementation and successful acceptance evidence recorded below | Hook effective evolved identity/relationship into shared prompt assembly and prompt inspector. Initial seed remains legible, not overwritten. | P01, P08, A11, A13 | Equivalent context across modes; no extra blocking model calls; explicit conflict precedence. |

### Phase 1 final acceptance

Tasks **1.1, 1.2 and 1.3 are ACCEPTED** by the owner for final integration, commit and push. D1–D12 are recorded in `COMPANION_PHASE1_DESIGN.md`, including approved D7/D8 and deferred `self_model` (modified D11). Acceptance relies on the completed validation below: Task 1.2 PostgreSQL 50 passed / focused regressions 241 passed; Task 1.3 PostgreSQL 62 passed / default backend 237 passed / frontend 79 passed / TypeScript clean, with unchanged wider baseline failures and expected mutation detections. These validations are not repeated for Git finalization. Known limitations below remain applicable. Phase 2 is not started.

### Task 1.2 implementation evidence (2026-11-07)

- **Scope:** P01/P08 and A03/A04/A07/A09/A15/A16 storage and lifecycle foundation implemented. No claim of experience-driven personality development: identity evolution remains Phase 3, relationship depth development remains Phase 5.
- **Models/migration:** `CompanionIdentityModel` and `CompanionRelationshipModel`, one row per owned companion; Alembic **`a12f907bc341`** extends `f687a630a8d1` as the single head. **No `self_model` column**, per modified D11. Configuration and legacy session working identity/mood stay intact.
- **Lifecycle:** companion creation inserts both rows atomically; shared preparation reads them with the companion in one joined query, without read-side inserts or new prompt text. New sessions seed from configuration plus approved stored adjustments. Existing relationship API/response/UI contract targets companion state; companion-less sessions retain legacy storage. Session deletion preserves durable state; deleting a companion with conversations returns 409; new owned rows cascade only when their companion is deleted.
- **Compatibility:** migration keeps all original records, copies deterministic latest-owned-session relationship settings, logs conflicts and chosen source IDs, backfills factual interaction timestamps/counts excluding interrupted assistant turns, and never promotes session drift. Read defaults support companions missing durable rows.
- **Concurrency:** SQL atomic count/version increments occur last before existing completion commits; failed/interrupted turns do not increment. Identity ORM versions reject stale writes. Settings creation serializes on that companion's parent row, preventing duplicate relationship records; settings updates retain last-writer-wins behavior. Unrelated companion updates are verified to proceed while another companion is locked.
- **Acceptance:** isolated RAM-backed PostgreSQL 16 + pgvector, built only with Alembic. Storage/migration acceptance 11 passed; timeline migration 9 passed; completion persistence 19 passed; PostgreSQL lifecycle 11 passed. Tests cover schema/default comparison, backfill, downgrade/re-upgrade, existing-table refusal, restart/new-session reload, ownership, room isolation, atomic rollback, concurrent sessions, duplicate prevention, deletion, and a frozen pre-1.2 seed/prompt golden fixture.
- **Default regressions:** 241 passed, 1 PostgreSQL skip, 1 known config test deselected, covering Phase 0, soul/identity, ritual/timeline continuity, provider routing, retrieval, batching and avatar backend. The initial full requested run reproduced 1 config-auth failure and 41 auth-fixture event-loop errors; an untouched `6c2f019` archive reproduced exactly the same set. `test_config_integration.py` also has a preexisting collection error importing absent `chatbot.config.Config`, reproduced on that archive. No assertions weakened or unrelated failures repaired.
- **Performance:** one statement per companion load, unchanged. Local median over 30 alternating loads was approximately 0.216 ms old lookup / 0.308 ms joined lookup in one acceptance run; measurements vary slightly between runs. No additional inference/embedding requests. Actual avatar latency was **not** revalidated.
- **Historical Task 1.2 boundary (superseded by completed Task 1.3 below):** Task 1.3 was then PLANNED: relationship prompt integration, removal of copied relationship/core-values defaults, room identity fallback changes and inspector precedence are deferred. No commit or push performed. Development/production databases and existing volumes were not used.

### Task 1.3 implementation evidence (2026-11-07)

- **Decisions:**
  - **D7 approved with clarification:** stored, evidence-based depth only; interaction counts are factual history, not intimacy; no invented stage.
  - **D8 approved:** generic core values removed.
  - Both are recorded in `COMPANION_PHASE1_DESIGN.md`.
- **Scope:** P01, P08, A11 and A13 prompt integration through the shared `CompileContextStep` used by standard chat (fast and full path), SSE and spoken chat. No Phase 2–6 behavior; no evolution algorithm; random drift and mood stay session-scoped.
- **Behavior:**
  - The configured personality reaches the prompt unchanged.
  - Durable trait adjustments seed each new session's working traits.
  - The durable relationship reaches the prompt as the user's configured preferences, plus a factual history line once turns exist. It has no stage, depth or stage guidance.
  - The generic `core_values` are gone, including from stored legacy session rows, via a read-only prompt view.
  - Configuration copies and copied relationship defaults are no longer repeated in companion sessions.
  - Unseeded room sessions resolve the actual companion.
  - Companion-less sessions are unchanged (D9).
- **Inspection:** the trace prompt is still the exact provider input. The `compile_context` trace step records `identity_sources` (configured / evolved / relationship / session), shown in `PromptInspector`'s new "Identity Sources" panel.
- **Tests:**
  - New `tests/test_persistent_identity_integration.py` (12): real `chat`, `stream_chat`, avatar `_spoken_turn`, `create_session` and `create_room` on real SQLAlchemy sessions; only the provider is faked.
    - Identical standard, streaming and spoken system prompts for equivalent state.
    - New sessions reuse the relationship and its history.
    - Room identity, companion and user isolation.
    - Mood and drift never becoming durable.
    - Inspector equal to provider input.
    - Fast path.
    - One generation, one embedding batch and one durable-state query per turn.
  - New `react_ui/tests/identitySources.test.ts` (2).
  - The Task 1.2 golden seed test now asserts the frozen seed and prompt minus exactly the approved D7/D8 removals.
  - Mutation checks: removing the relationship, the prompt view or room seeding each fails the expected tests.
- **Results:**
  - Disposable PostgreSQL built only by Alembic: **62/62** (Task 1.3 12, Task 1.2 storage 11, timeline migration 9, completion 19, lifecycle 11).
  - Default environment, focused set: **237 passed**, 19 PostgreSQL-only skipped.
  - Frontend: **79/79**; `tsc --noEmit` clean.
  - Wider non-integration suite: failure set **identical** to the committed Phase 0 baseline (`6c2f019` archive).
- **Known pre-existing failures:** `test_auth_companions` errors only when run after other async test files; it passes 41/41 alone. `test_identity_config::test_config_api_identity_endpoints` returns 401 because the test has no auth override. `test_companions` has 2 failures, already failing at the baseline, with the failure mode changed by Task 1.2's `add_all`. `test_config_integration.py` fails at collection. None of these was caused by Task 1.3.
- **Performance:** `compile_context` +18 µs median per turn. No added query, LLM call or embedding call. Live avatar latency was not re-measured.
- **Limitations:** a durable adjustment reaches an open session only from its next session; relationship depth has no evidence source until Phase 5; mood-engine guidance is unchanged.

**Review candidate:** E09 synthetic formative experiences — only after approval. No fabricated observed user history.

## 5. Phase 2 — Durable semantic memory and consolidation

**Goal:** meaningful, reliable memory without per-message synchronous LLM extraction. Reuse `messages`, vector storage and existing background/consolidation components where practical.

| Task | Status | Scope | Requirements | Acceptance evidence |
|---|---|---|---|---|
| **2.1** | IMPLEMENTED — design and configured extraction routing | Select durable background dispatch mechanism in existing PostgreSQL environment; idempotency, retries, concurrency, cost limits; no new framework. Choose extraction provider/model via explicit approval. | A05, A06, A10, A16 | Restart/retry behavior and failure model defined; bounded budget; no extra TTFT model call. |
| **2.2** | IMPLEMENTED — durable runtime integrated | Implement durable completed-turn work dispatch and recovery; all chat modes contribute exactly once. | A01, A06, A09 | Restart loses no committed work; retries do not duplicate memories; cancellation excluded appropriately. |
| **2.3** | IMPLEMENTED — extraction, canonical facts, evidence and reconciliation | Replace regex-only fact extraction with batched semantic extraction and provenance-aware consolidation across fact/episode scopes. | A05, A07, A08, P10 | Realistic corpus precision/recall, corrections, temporal changes, conflicting facts tested; no blocking inference. |
| **2.4** | IMPLEMENTED — isolated retrieval and reversible prominence; P11 erasure decision remains REVIEW | Memory weighting, indexing/retrieval and selective forgetting: decide query eligibility/compaction vs hard deletion; verify pgvector `<->` vs `<=>` and index configuration explicitly. | P11, A07, A08, A10 | Relevant retrieval beats baseline; no cross-room leaks; long-run growth bounded without unapproved deletion. |

**Do not:** add one LLM call per message, default to synchronous consolidation, or retain the unused regex extractor as a second competing authority.

### Phase 2 completion evidence

Runtime integration `5a00036` is extended by the Phase 2 completion commit on `feature/companion-phase2-integration`, incorporating lifecycle checkpoint `e1c5761`. `ConsolidationWorker`'s destructive prune adapter is disabled; deterministic reconciliation runs in the durable extraction transaction, without another model call or queue. Canonical facts keep immutable evidence from every contributing turn; corrections and temporal changes create explicit historical links; unresolved conflicts remain distinct. Retrieval enforces persisted current/history eligibility and trusted ownership scopes. Memory controls preserve correction history and authenticate ownership.

Additive migration `d41e8a7c502b` extends `c93a14e2786b`: lifecycle fields/indexes, `memory_evidence`, and `memory_relations`. Existing rows are retained with active defaults; no historical scope/evidence backfill is performed. Historical facts without reliable scope are not guessed into new consolidated scopes. Source evidence blocks a destructive downgrade.

Acceptance A–L: **59 passed, 1 skipped after targeted corrections**, using mocked providers and a new isolated PostgreSQL/pgvector fixture. The initial single pass had 58 passes and one failure; only the failed test was rerun while correcting reinforcement precision and async pin persistence. The skipped default-server characterization is redundant with the executed real fixture-backed vector/isolation tests. The previously failing independent cross-turn assertion now passes unchanged. No full suite, paid inference, deployment or main modification. **P11 remains REVIEW**: only reversible prominence/eligibility, compaction and existing explicit deletion semantics are implemented; no automatic physical erasure or irreversible retention policy.

## 6. Phase 3 — Autobiographical continuity and experience-driven evolution

**Goal:** meaningful experiences cause gradual, explainable, persistent changes; no pseudo-random trait drift masquerading as learning.

| Task | Status | Scope | Requirements | Acceptance evidence |
|---|---|---|---|---|
| **3.1** | IMPLEMENTED — shared experiences as semantic memories (`chatbot/memory/autobiography.py`) | Build/reuse narrative episode and shared-experience records with provenance, connections, salience, revisions and durable relationship references. | P07, P10, A07, A08, E05 | Multi-session shared experiences recalled with correct participant/source and timeline. |
| **3.2** | IMPLEMENTED — contract in `COMPANION_PHASE3_SELF_MODEL.md`; `chatbot/soul/self_model.py`, migration `f3a9d6b1c4e7` | Define compact self-model and identity update contract, anchors/inertia, evidence thresholds and version-aware updates; no separate governance engine. | P01, P04, P07, E01, E06, A16 | Update semantics, source evidence and conflict handling approved. |
| **3.3** | IMPLEMENTED — reflection in the durable memory job, shared prompt section, random drift retired | Connect background reflection and evolved opinions/interests to durable identity and prompts. Remove/retire random-drift behavior when replaced. | P04, P07, P09, E05, E06, A11 | Meaningful experiences drive change; simple chatter doesn't cause random personality mutation; cross-session continuity. |

### Phase 3 completion evidence

Integration branch `feature/companion-phase3-integration`. Experiences (3.1) and companion self-observations share the **one** existing extraction call (`self` and `experiences` lists in its response; output bound unchanged at 700 tokens). `chatbot/memory/self_observation.py` validates observations (verbatim evidence from the companion's own reply, independent reasoning, no roleplay/fiction/hypothetical, no unsupported claims); `_apply_self_reflection` applies them to `chatbot/soul/self_model.py` inside the existing durable job transaction (companion-bound sessions, durable non-scene rooms, reply attributed to that companion; idempotent via receipt replay). The established views reach standard, SSE and spoken prompts through the single `load_companion_inputs` row as a `DEVELOPED SELF` section after `IDENTITY` (held items only, at most 900 characters, listed in Prompt Inspector `identity_sources.self_model`); an empty or tentative-only self-model leaves the prompt unchanged. The slow-trait random walk in `IdentityState.apply_evolution` is retired; session mood remains. Automatic numeric trait adjustment stays deferred.

Acceptance (`tests/test_phase3_acceptance.py`, the eight scenarios of `tests/fixtures/phase3_behavioral_acceptance.json`) with mocked providers: SQLite and an isolated Alembic-built PostgreSQL 16/pgvector (migration `f3a9d6b1c4e7` upgrade, empty default, downgrade refusal, real concurrent jobs). Known pre-existing failures on main, unchanged: `test_timeline_migration::test_deleting_a_session_deletes_its_timeline_entries`, `test_durable_handler_storage_errors::test_storage_preserves_explicit_source_type`, `test_identity_config::test_config_api_identity_endpoints`, three `test_conversation_lifecycle` tests.

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
| **5.1** | IMPLEMENTED on `feature/companion-phase5-integration` | Persist and restore temperament-aware mood across turns/sessions; decay over elapsed time; retrieve, revise and reactivate bounded, provenance-backed emotional associations through the durable extraction job. Relationship history remains factual; associations influence context without writing invented trust/depth scores. | P03, P08, A11 | `tests/test_phase5_integration.py`: durable receipt/retry, implicit unresolved recall, companion-scoped mood restoration, PostgreSQL association concurrency/revision and evidence checks. Source mood component's 30 focused tests passed before integration. |
| **5.2** | IMPLEMENTED on `feature/companion-phase5-integration` | Assemble character-specific social context from configured personality, factual relationship history, developed views, retrieved experiences and emotional associations; deliver it through shared standard, SSE and spoken preparation. No response selector or extra inference/embedding call. | P14, E04, P15 | `tests/test_phase5_integration.py`: shared context and completion behavior across delivery modes, durable roleplay exclusion, room/companion/user isolation, and prompt lookup without embeddings. Bounded OpenRouter evaluation used 12 generations; distinct voices and nuanced reconciliation/risk responses appeared, while implicit disagreement reactivation on a generic greeting was inconclusive. |

**Phase 5 integration evidence (2026-11-08):** the full existing Alembic chain upgraded on an isolated PostgreSQL 16/pgvector database. Focused integration checks verified that two synchronized writes preserve two distinct `memory_evidence` rows, revisions supersede without erasing evidence, and companion/room/owner boundaries filter retrieval. One local PostgreSQL prompt-preparation sample measured about 5.8 ms incremental for Phase 5 association/social context (including its bounded database lookup); it made no embedding request. The real-provider sample is qualitative only and is not a substitute for a larger blinded behavioral evaluation. P15 shared traditions and A17 conversational maturity remain **REVIEW**; neither was approved or implemented as a policy.

**Review:** shared traditions P15; conversational maturity A17. No unsolicited ideological or interpersonal-conduct governance additions.

## 9. Phase 6 — Distinctive expression and optional TTS metadata

| Task | Status | Scope | Requirements | Acceptance evidence |
|---|---|---|---|---|
| **6.1** | IMPLEMENTED on `feature/companion-phase6-integration` | Make dialogue fingerprint (humor, syntax, rhythm, imperfection and pacing) recognizable and gradually adaptable from seeded character. | P06, P16, A15 | Blind tests distinguish character voice; preserves intelligibility without overconfigured sliders. |
| **6.2** | IMPLEMENTED — spoken capability gate, typed trailing metadata and clean streaming | Define clean dialogue + optional typed per-segment emotion/tone/pace/emphasis metadata, map to TTS *only after verifying engine controls*; text UI receives dialogue only. | P03, P06, A12 | Streaming parser compatibility and clean output; graceful fallback without expressive TTS; no model call added by default. |

**Phase 6 integration evidence (2026-11-08):** the shared `CompileContextStep` now uses the completed fingerprint and character-rhythm components from existing authorized state. Legacy formatter/pacing interfaces remain available; the shared assembler suppresses their duplicate directives. `tests/test_phase6_integration.py` exercises the ten supplied fixture scenarios, arbitrary protocol chunk boundaries, malformed/partial metadata, literal markers, cancellation, deadlines, capability fallback, unchanged voice identity, clean completed spoken persistence and durable job scheduling, cross-mode context and unchanged inference counts. The directly affected Phase 5 emotional/relationship cross-mode test also passes. These mocked checks demonstrate context and protocol behavior, not blind recognition of generated voices.

Performance is off by default (`AVATAR_PERFORMANCE_ENABLED=false`); enabling it requires a spoken sink declaring a verified control. The shipped clone runtime declares only Chatbot-side pauses. `AVATAR_PERFORMANCE_DEBUG` permits explicit protocol testing with unsupported fields ignored. No Qwen model/voice substitution or Fish adapter is included. Visible paragraph breaks are preserved; metadata is retained only in existing turn diagnostics. Parser sample: approximately 0.007 ms first-text processing and 0.073 ms total for a short two-trailer response (97 metadata characters). Actual model output-token and hardware first-audio costs have not been measured. No extra inference, embeddings, queries for expression planning, or extraction-budget changes were introduced.

**Boundaries:** Qwen and Fish Audio Drama 3 are external renderers of character performance, not sources of identity. Do not refactor AVTR/TTS/voice selection outside an approved adapter integration.

## 10. Phase 7 — Evaluation, integration and performance

Evaluation is **continuous**, not deferred until the end. Phase 7 packages and closes the evidence.

| Task | Status | Scope | Requirements | Acceptance evidence |
|---|---|---|---|---|
| **7.1** | ENGINEERING INTEGRATED on `feature/companion-phase7-integration`; **real-model evaluation INCOMPLETE; blind judgments NOT COLLECTED** | Build/extend three-character, shared-scenario evals (~30 scenarios baseline); test blind recognizability, disagreement, initiative, emotional continuity, factual memory, corrections and cross-session identity. | E08, A14 | Harness, thirty-scenario dataset and rubric integrated; scripted acceptance recorded below. Baseline-vs-improved behaviour, recognizability and personality quality are **not** established: no completed real-model run and no independent ratings exist. |
| **7.2** | INTEGRATED on `feature/companion-phase7-integration` (targeted evidence; obsolete-duplicate removal not undertaken) | Targeted integrated checks for restart/retry, memory cost, throughput, room/user isolation, prompt fidelity and TTFT; remove verified obsolete duplicates. | A09, A10, A13, A14, A16 | Reliability and deterministic-overhead evidence recorded below. "Demonstrated behavioral gains" remains unproven pending 7.1's real-model and blind evaluation; provider TTFT was not measured. |

### Phase 7.2a reliability evidence (2026-11-09)

A09/A10/A13/A14/A16: targeted checks ran against a fresh PostgreSQL 16/pgvector instance on a non-development port, built only by `alembic upgrade head` through `e5f2a7c9b8d4`. Across the initial selected suites and directly affected reruns, **86 distinct checks passed**: 74 existing integration/database checks, six new PostgreSQL reliability checks, and six directly affected thread reconciliation checks. No full repository suite or paid provider was used.

Existing coverage verified lease recovery, bounded retry and terminal failure, duplicate delivery, receipt reuse, transaction rollback, concurrent canonicalization/self-model/emotion writes, version conflicts, provenance, actual pgvector ownership/companion/room/session filtering, roleplay exclusion, mood restoration, and standard/SSE/spoken provider-prompt fidelity and clean performance persistence. A new fault-injection check fails after all memory/self/thread/emotion effects, verifies the entire effects transaction rolls back, retries from the persisted receipt with no second extraction call, and reloads identity, relationship, facts/evidence, experience, opinion, thread, emotion and mood from a fresh Python process.

Demonstrated fixes are confined to conversational-thread persistence: serialize initially empty scopes using the existing companion parent row; refresh locked ORM rows before updating evidence; retain older observations as historical evidence without reopening resolved topics or applying stale resolutions; deduplicate evidence by job/receipt slot while preserving original slots. No migration, dependency, worker or personality-policy change was needed. One pre-existing Phase 1 fast-path assertion omitted the approved Phase 5 mood steps; its exact expected order now includes restoration, update and persistence without weakening the assertion.

Limits: this evidence establishes targeted storage/recovery/isolation and prompt correctness, not exhaustive failure injection, long-run load behavior or real-model conversational quality. Existing bounded evidence-retention policies are unchanged. No development records were modified; no commits or pushes were made by this assignment.

### Phase 7 integration evidence (2026-11-09)

Integrated on `feature/companion-phase7-integration` from `main` at `019b413`, merging `feature/companion-phase7-behavioral-eval` (7.1a harness), `feature/companion-phase7-reliability` (7.2a), `feature/companion-phase7-performance` (7.2b) and `feature/companion-phase7-scenarios` (7.1b dataset and rubric). The merge had no conflicts. The only production change in Phase 7 is the 7.2a conversational-thread fix. There is no migration, no dependency, no extra LLM or embedding call, and no personality-policy change. No paid inference was used during integration.

The evidence is kept in four separate categories, and no category is evidence for another.

**A. Infrastructure correctness (verified).**
- *Harness (`tests/evaluation/`, 34 focused tests).*
  - Each provider request is written to a fsync'd ledger before it is sent, and each reply as soon as it returns.
  - An interrupted run keeps its replies. Resume replays them and never regenerates.
  - Errored or unanswered requests are re-sent only with `--retry-unanswered`.
  - Budgets count all earlier requests. Limits below 1, and unbounded provider runs, are rejected.
  - A ledger is never overwritten, and a different configuration or model cannot resume it.
  - Cell identity is scenario/condition/character/session.
  - Blind packages strip names, and the ground truth lives in a separate file.
  - Scores are absent when no rater is present. Baseline comparison refuses mismatched models, sources, probes or configurations.
- *Integration defects found and fixed (all in the harness, none in production):*
  - The loader's executability rule disagreed with the runner, so `scenario_5_new_evidence_can_revise_previous_opinion` ran zero cells. There is now one probe rule: a session's final user turn.
  - Phase 3 later-session probes, Phase 4 `contrast_turns` and `cross_session_episodes`, and Phase 5 condition and test-case stages were not mapped. Eight scenarios were wrongly "skipped".
  - Seeded thread ids collided across companions because the seed job id was shared. Production derives thread ids from `(job id, slot)`, and its job ids are unique per companion turn.
  - Association seeds produced labels production correctly rejected as `bad_emotion`.
  - Phase 5 `two_relationship_stages` were not applied to their stage conditions.
  - The loader fallback name "Companion" was treated as an identifying label.
- *Seeding through existing writers.*
  - Relationship row (`completed_turns`, `depth`, interaction dates).
  - Self-model interests and opinions.
  - `semantic_facts` with `memory_evidence`.
  - `apply_emotional_associations`.
  - `apply_thread_observations`.
  - Every fixture field with no production representation is recorded per cell as `not_applied`, with its reason: `trust_level`, stage labels, humour style, free-text situation descriptions, and condition-specific valences, which are expected interpretations rather than state.
- *Retrieval stand-in.* Retrieval in the harness uses an owner-, companion- and room-scoped, unranked stand-in, because the evaluation makes no embedding calls. Production vector ranking is not exercised.

**B. Scripted acceptance (the fixtures' own example replies; tests the harness and scoring, not any model).**
- *Coverage.* All five fixtures load: 68 scenarios, including the 30 Phase 7 scenarios with 5 in each of categories A–F, three distinct companions, and the 8-dimension rubric. The Phase 7 dataset was not edited. All 68 run, giving 144 cells and 0 skipped.
- *Totals.* **141 passed, 1 failed, 2 incomplete.** By scenario: 66 complete, 1 partial, 1 incomplete.
- *Phase 7 alone.* 90 cells: 89 passed and 1 failed.
- *Classification of the 7.1a non-passes:*
  - `scenario_10_cross_mode_consistency` and `scenario_5_adaptive_response_depth`: **harness defects (A), fixed narrowly.**
    - In the first, "anything else" matched outside an offer.
    - In the second, list structure was counted as drift in reply to an explicit "Can you explain how …" request. Production's own directive forbids lists only "when the user is chatting naturally". Lists in casual chat are still drift.
  - `scenario_16_shared_achievement`/Mercury: **fixture assumption (B).** The fixture's example reply contains "keep in mind that". The criterion is unchanged and the cell stays failed.
  - `scenario_8_streaming_performance`/Kore and `scenario_2_related_experiences…`/Kore (`session-201`): **missing scenario input (D).** The fixture has no expected reply for these cells. Nothing was fabricated, and they stay incomplete.
- *Behavioural limitations observed (C, production behaviour, not defects):*
  - Open threads and emotional associations are relevance-gated, so `scenario_09_initiative`'s threads are seeded but not surfaced for an unrelated message.
  - `scenario_5_teasing_versus_criticism`'s two conditions receive byte-identical prompts, because trust, humour style and the dispute context have no production state.

**C. Real-model evaluation: INCOMPLETE.**
- The bounded 7.1a run (3 companions × 4 scenarios, at most 12 generations) consumed **about 10 of the 12 authorised generations** across two aborted attempts. **No reply was recorded.**
  - Each attempt sent 5 requests and crashed in measurement after the provider answered (`StopIteration` from a cast-override lookup).
  - Results were written only at the end of a run, so nothing was kept.
  - The second attempt happened because `--max-generations 0` was read as unset.
- The owner decided that the loss counts against the authorisation, that the run is not to be restarted, and that the lost outputs are not to be reconstructed. `tests/evaluation/results/initial-real/evaluation_status.json` records this, and the preset's ledger holds an `unrecorded_requests: 10` entry, so the code allows only 2 more generations there.
- No completed real-model evaluation exists, and no baseline-versus-improved behavioural comparison has been run.

**D. Independent blind judgments: NONE COLLECTED.** The harness produces the anonymous package and judgment template with the rubric's dimensions and name-stripped identification markers. It scores only complete, attested raters, and never for scripted replies. Recognizability is **not verified**.

**Reliability (A09/A10/A13/A16).** One focused integration pass ran against a fresh PostgreSQL 16/pgvector database on 127.0.0.1:55437, built only by `alembic upgrade head` to `e5f2a7c9b8d4`. The development database was untouched.
- *Result:* 305 tests collected, **298 passed**.
  - Covered: the 6 Phase 7.2a PostgreSQL reliability checks; the five thread fixes; rollback with fresh-process restore; retry and restart recovery; ownership and room isolation; standard/SSE/spoken prompt fidelity; no leakage of performance metadata; unchanged provider call counts; the 11 performance invariants.
- *7 failures, all pre-existing:* each fails identically on the untouched baseline `019b413` against the same database, and none involves Phase 7 code.
  - `test_completion_persistence`: two fast-path assertions that predate the Phase 5 mood steps.
  - `test_phase4_behavior_integration` scene/roleplay: expected prompt predates the Phase 6 expression layer.
  - Two `test_phase4_conversational_threads` SQLite fixtures that lack `semantic_facts`.
  - `test_durable_handler_storage_errors`: a `SimpleNamespace` double without `.get`.
  - `test_timeline_migration`'s delete test: runs on its own older-revision database.
- They are recorded here and were not changed by the integration.

**Performance and cost (7.2b; `docs/verification/phase7_performance_evaluation.md`, reproducible with `scripts/phase7_measurements.py`).**
- *Deterministic preparation overhead* is about **3.7–4.2 ms median per turn** in the controlled sample: identity, relationship and thread load; mood restoration; association retrieval; and behaviour, fingerprint and pacing assembly. This is application overhead only. **It is not provider latency, which was not measured.**
- *Call counts:* one conversation generation per turn, one embedding batch per turn, and one provider call per extraction job, with a replayed job costing 0 calls. All are unchanged.
- *Prompt growth:* about 500 estimated tokens since Phase 1, all section-capped.

**Extraction output limit (owner decision pending; not changed).**
- *Current state:* `LLMExtractionLimits.max_output_tokens` remains **700**.
- *Measured limitation:* the maximal legitimate full-category extraction (8 facts, 2 experiences, 3 self-observations, 2 threads, 2 emotions) is about **1,063 estimated tokens**. Typical turns are 19–419.
- *Failure mode:* the provider truncates (`finish_reason=length`), the JSON becomes unparseable, and the whole response falls back to the regex heuristic. That keeps plain user facts but **loses experiences, self-observations, threads and emotional associations** for that turn. There is no partial parse and no retry.
- *Recommendation (GLM):* **1,100 tokens.**
  - Typical extractions are unaffected, because the limit is a ceiling.
  - The expected maximum increase is up to **400 output tokens on the turns that reach it**.
  - `max_response_chars` (12,000) already accommodates it.
- *Uncertainty:* counts are characters/4 estimates, not tokenizer counts, and JSON-dense output usually tokenizes at fewer characters per token. The real maximal turn may therefore exceed 1,063, and 1,100 may not cover every maximal turn. Verify against the configured extraction model's tokenizer or reported usage before or after approval.
- *Not done:* no retries and no split extraction calls were added.

**REVIEW items stay open and unchanged:** P11 erasure, P12, P15, E07, E09 and A17. Nothing in Phase 7 approves or implements them.

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
