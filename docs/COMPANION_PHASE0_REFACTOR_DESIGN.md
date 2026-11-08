# Companion Evolution — Phase 0 Refactor Design (Task 0.1; amended for Task 0.2)

**Repository:** `~/dev/mike/chatbot/python/chatbot`
**Status:** Phase 0 **ACCEPTED** for integration (2026-11-07). Task 0.1 design, amended by **Addendum A** (decisions recorded for Task 0.2) **Addendum B** (Task 0.3 as implemented) and **Addendum C** (timeline migration and final PostgreSQL acceptance). The addenda govern where they conflict with the body.
**Date:** 2026-11-07
**Requirements covered:** A01 (unified semantics), A02 (delivery separation), A09 (isolation), A10 (resource discipline), A13 (prompt visibility), A16 (state integrity).
**Authority:** `docs/COMPANION_EVOLUTION_REQUIREMENTS.md`, `…_ARCHITECTURE.md`, `…_IMPLEMENTATION.md`. Audit input: `docs/COMPANION_MEMORY_CONTEXT_AUDIT.md`.

> **Missing input.** `docs/COMPANION_EVOLUTION_PLANNING_RESEARCH.md` does not exist in the working tree, in git history, or under `/home/mike` (searched). Its claims were taken only as relayed by the architecture document (§2, §3.4) and re-derived from source. Nothing in this design depends on that file.

---

## Addendum A — Decisions recorded for Task 0.2 (supersede conflicting text below)

Recorded 2026-11-07 after the Task 0.1 design review and an independent architecture review. Where this addendum conflicts with §1–§16, **this addendum governs**. The original text is left in place as the record of the 0.1 analysis.

| # | Decision | Effect on this document |
|---|---|---|
| A1 | **The fast path is preserved completely.** `_route_by_intent`, `FAST_PATH_STEPS`, `ROOM_FAST_PATH_STEPS`, `DEFERRED_STEPS`, `_FAST_PATH_INTENTS`, `_run_fast_path`, `run_fast`, `enqueue_deferred` and standard chat's deferred extraction stay. Standard chat keeps its fast path; streaming and spoken keep their own full step loop. The fast path is a legitimate latency feature, not duplication. It is not enabled in any new delivery mode. | **D2 reversed. D14 reversed** (nothing is deleted in 0.3 either). §2.2/§2.3 describe current behavior and remain accurate. §5.1's `PREPARE/GENERATE/COMPLETION` step groups and `TurnPipeline.iter_steps` are **not built**; step execution is not part of preparation. |
| A2 | **Companion configuration is the authoritative personality source.** Neither `DEFAULT_PERSONALITY` nor `{}` is automatically correct. Where no companion configuration is available, each mode keeps its established fallback. | **D1 modified.** See §A.2 below. |
| A3 | **Mood / user state:** consolidating preparation must not give standard chat new stateful behavior. Current semantics are preserved (standard chat does not read `sessions.mood_state/user_state/conversation_mode`; streaming does). | **D3 withdrawn → unresolved (U1).** The C16 snapshot-refresh idea is also dropped. |
| A4 | **Room prompts:** existing first-turn room behavior is preserved; no every-turn injection and no removal for parity. | **D6 withdrawn → unresolved (U2).** Room resolution, `_build_room_system_prompt`, `_inject_room_system_prompt` and the standard-only in-memory history fallback stay in `chat.py`; streaming keeps its session-bound room filter. |
| A5 | **Provider formatting is unified** in one `prepare_provider_call`, without breaking model-specific formatting, thinking modes or existing streaming providers, and verified against real adapter payloads. | **D4 stands.** |
| A6 | **Transactions, commit policy, session lifetime, rollback and post-turn persistence belong to Task 0.3.** | **D5, D7, D8, D12 (entirely), D13 → Task 0.3.** The "commit pre-turn embedding writes" part of D12 is *not* in 0.2. |
| A7 | **No speculative infrastructure:** no `CompletedTurn` worker or hook, no future-personality plumbing. `PersonaCache` and existing performance features stay. | §7.7 (`CompletedTurn` and reserved hooks) is **not built** in 0.2; revisit under 0.3. |

### A.1 Correction to D12 (found while implementing 0.2)

§11.3 and D12 proposed committing the pre-turn embedding batch where it is generated. In production the retrieval engine uses its own session factory (`app.py:48`, `SessionAwareVectorStore`), so uncommitted straggler vectors are invisible to the same turn's retrieval today. Committing *before* retrieval would make up to 20 recent messages retrievable in the turn that embeds them, duplicating the history window in "Retrieved memories". That is a retrieval-behavior change, not just a persistence fix, so it is withheld from 0.2 and must be designed with 0.3's transaction work (for example by committing after retrieval, or by moving embedding after persistence).

### A.2 Canonical personality (as implemented in 0.2)

How the two inputs reached the prompt (verified):

| Mode | `persona` input | Resulting system-prompt persona text |
|---|---|---|
| Standard (before) | `DEFAULT_PERSONALITY` | Styled block: `Persona / Identity: Companion / Tone: neutral / Traits: empathetic, analytical, clear / Backstory: A helpful, thoughtful chatbot companion.` |
| Streaming/spoken (before) | `{}` | `You are a helpful, neutral assistant. Respond clearly, accurately, and respectfully.` |

In both, the companion's real configuration arrived only through `character_profile` (name, backstory, sliders, humor, communication style, favorite topics). The companion's free-text `persona` prompt (labelled in the companion form "how this companion presents itself"), its `description`, and its linguistic fingerprint (`punctuation_habits`, `sentence_length_profile`, `emoji_style`, `vocabulary_density`, `slang_profile`) are stored and editable but reached the prompt in **no** mode.

Implemented: `conversation.companion_to_persona(companion)` maps those attributes onto keys `PersonaPromptAssembler` already understands (`name`, `persona`→`custom_instructions`, `description`, and the five fingerprint fields). Attributes `character_profile` already carries are not mapped, so nothing is rendered twice. `conversation.resolve_persona(companion_persona, fallback)` returns the companion persona when present, otherwise the mode's fallback: standard chat keeps `standard_persona_fallback` (a configured session/app persona, else `DEFAULT_PERSONALITY`), streaming/spoken keep `{}`.

**Product-visible consequence (needs owner confirmation, U3):** for companion-bound sessions the persona text, description and fingerprint now reach the prompt in every mode, and the generic "helpful chatbot companion" block (standard) / generic assistant sentence (streaming) no longer precedes the companion's own identity. To narrow what flows into the prompt, edit `_COMPANION_PERSONA_KEYS` in `chatbot/api/conversation.py`.

### A.3 Task 0.2 as implemented

- New `chatbot/api/conversation.py` holds only logic that was duplicated verbatim: `first_state_value`, companion profile/persona loading (`load_companion_inputs`, `companion_to_profile_dict`, `companion_to_persona`, `resolve_persona`), `load_session_identity`, `load_db_session_history`, `build_turn_context` (one metadata builder replacing `_build_metadata` and `_stream_metadata`/`_seed_stream_history_metadata`), `prepare_memory_workflow` and the embedding batch helper (moved unchanged).
- `core/turn.py`: `ProviderInputs` and `prepare_provider_call(ctx)`, used by `CallProviderStep` and by `_stream_provider_tokens`. Streaming now sends `format_prompt_for_model` output exactly as standard chat does and records the same `llm_*` inspection metadata.
- `chat.py` and `streaming.py` call the shared functions. Standard chat's fast path, `_run_pipeline`/`build_default_pipeline` (persona cache, background queue), room handling and persistence are unchanged. Streaming's step loop, persistence, rollback and post-stream writes are unchanged.
- One latency improvement within preparation: an avatar-bound turn used to load full history and identity at the endpoint and again after `coordinator.begin()`; the endpoint now skips the load it would discard (`history=None` means "load after begin").

### A.4 Genuine differences between modes that are preserved and reported

| ID | Difference | Status |
|---|---|---|
| U1 | Standard chat ignores saved mood/user state/conversation mode; streaming/spoken honor them. | Preserved. **Decision needed:** should standard chat read saved state (the same companion and session)? |
| U2 | Room-type prompt reaches the model only on a room's first turn, standard chat only (injected into history, then dropped by the working-memory window). Streaming never has it. | Preserved. **Decision needed:** intended behavior (every turn? all modes?). |
| U3 | Companion persona text/description/fingerprint now reach the prompt (A.2). | Implemented per A2. **Confirm** the field set. |
| U4 | No-companion fallback persona differs (standard `DEFAULT_PERSONALITY`, streaming `{}`). | Preserved. Decision needed only if a single fallback is wanted. |
| U5 | Rooms created by `create_room` link their auto-created session only through `Room.session_id` (`rooms/room.py:195-196`); standard chat resolves that room, streaming/spoken use only `Session.room_id` and see no room for those sessions. | Preserved. Decision needed: which resolution is correct for streaming/spoken. |
| U6 | Session snapshot for an avatar-bound turn is taken before `coordinator.begin()` waits for its predecessor (stale mood possible). | Preserved (C16). |
| U7 | Standard chat accepts a body `companion_id` (validated: 404 / 400 mismatch); streaming uses only the session binding. | Preserved (HTTP-edge parameter). |
| U8 | Loss of in-loop writes in streaming (F1) and all other completion differences. | **Task 0.3.** |


---

## Addendum B — Task 0.3 as implemented (supersedes the §7/§8/§14 completion design where it differs)

Recorded 2026-11-07. Addendum A's constraints still hold: the fast path and every standard/streaming/spoken capability are preserved. This task only fixes persistence and consolidates duplicated completion logic.

### B.1 Corrections to the Task 0.1 completion design

| Original design | What implementation showed | What was built |
|---|---|---|
| §7/§8: persist messages in their own session, then run effects (or commit effects afterwards) in a second session | Effects such as `PersistEmotionalStateStep` update the `sessions` row and keep a row lock until commit. Persisting messages afterwards in another session runs `UPDATE sessions SET updated_at` on the same row and **blocks on a lock its own task will release only after the persist** (an application-level deadlock in PostgreSQL; SQLite reported it as `database is locked` in the first real-session run). | A completed streaming turn's **messages and effects are committed as one transaction** on the generation session, the same model standard chat already uses. |
| D5: move effect steps after `response` and reorder SSE step events | Not needed to fix the defect. | **Not done.** Step order and SSE events are unchanged. |
| D12: commit the pre-turn embedding batch where it is generated | Would make straggler vectors visible to the same turn's retrieval (Addendum A.1). | The preparation transaction is committed **after retrieval and after the first token**, so those vectors become durable for the next turn and this turn's retrieval is unchanged. |
| §7.3 `EFFECT_POLICY`, `CompletedTurn`, `TurnOutcome` | Speculative for this task (Addendum A7). | Not built. Eligibility is one predicate, `_generation_failed(ctx)`. |

### B.2 Transaction ownership (streaming and spoken)

| Phase | Session | State |
|---|---|---|
| Preparation (history, identity, embeddings, retrieval) | `stream_db` | Opened by the first read. |
| Generation and spoken playback | none held | `end_preparation_transaction` commits after the **first token** (so first text is not delayed), then begins the effects transaction explicitly. `begin()` acquires no connection until used. |
| Post-turn effect steps in the existing loop (mood, emotional state, memory extraction, timeline) | `stream_db` effects transaction | Flush only. The transaction is begun explicitly because `_flush_or_commit` and `SQLAlchemyMemoryStorage` choose between flushing and committing by asking the session whether a transaction is open, and a session that has just committed reports none. |
| Eligible completed turn | `stream_db` | `add_turn_messages` + **one commit** = messages + effects. |
| Failed generation (`"Provider error:"` reply) | `stream_db` rolled back, then a fresh session | Error text saved as the reply, no effects. This keeps streaming's de-facto outcome (its effects were always discarded) and is a deliberate difference from standard chat, which commits effects for such a turn. |
| Interrupted spoken turn | `stream_db` rolled back, then a fresh session | Unchanged: heard prefix + marker, no effects, effect steps never ran. |
| Post-stream embeddings and identity update | own fresh sessions | Unchanged. |

Standard chat is unchanged: one request transaction, committed once.

### B.3 Duplicated logic eliminated

- Message rows, timestamp ordering, interruption marker and `sessions.updated_at`: one `conversation.add_turn_messages` (standard chat on the request session; streaming on `stream_db` or, for interrupted turns, a fresh session). `omit_empty_assistant` preserves streaming's existing rule that an empty confirmed reply stores no assistant row.
- Title scheduling and generation: one `conversation.schedule_title_generation` / `generate_conversation_title` with the session factory passed in (about 140 duplicated lines removed). Policy is unchanged: scheduled for standard and non-avatar streaming, not for avatar-bound turns, and dormant while sessions are created titled "New Chat".
- Timeline: `add_timeline_entry` accepts an optional `session`. `TimelineUpdateStep` passes the turn's session, so an entry joins the turn's transaction inside a SAVEPOINT (a failing entry cannot poison the transaction) and is committed or discarded with the turn. Previously it committed in its own session before the turn was saved, so an entry could exist for a turn that was interrupted, failed, or never persisted.

### B.4 Differences between modes that remain (preserved, not decided)

| ID | Difference |
|---|---|
| U9 | A provider-error turn: standard chat commits its effects (regex extraction runs on the error text); streaming/spoken save the reply without effects. Needs a product decision (earlier D7). |
| U10 | Standard chat holds its request transaction through the LLM call (its single atomic commit depends on it). Streaming no longer does. |
| U11 | A typed SSE disconnect before the turn is saved still persists nothing (earlier D8). |
| U12 | Post-stream embeddings remain 2 sequential per-message requests for streaming/spoken (none for standard). Moving to one batch is a separate optimization. |
| U13 | A standard-chat step failure still saves the error string as the assistant message; a streaming step failure still saves nothing. |

### B.5 Verification limits

PostgreSQL was not running and was not started (the project's data volume `chatbot_postgres-data` exists and holds the development data; the system clusters are down and lack pgvector). **No PostgreSQL integration test was executed.** `tests/test_completion_persistence.py` drives the real application code through real SQLAlchemy `AsyncSession`s on a SQLite file using the standard pysqlite `BEGIN`/`SAVEPOINT` recipe; it verifies SQLAlchemy session semantics and transaction ownership, not PostgreSQL behavior (without that recipe SQLite wrongly keeps rows written in a savepoint and would hide the defect).


---

## Addendum C — Timeline migration and final PostgreSQL acceptance

Recorded 2026-11-07.

### C.1 The defect

`RelationshipTimelineEntryModel` (`chatbot/continuity/timeline.py`) and its writers existed, but **no Alembic revision created `relationship_timeline_entries`** (the initial revision creates a different table, `timeline_entries`; the model is also not imported by `migrations/env.py`, so it was never in Alembic's metadata). On a database built from migrations every timeline write failed and was logged and skipped. It was found when PostgreSQL acceptance needed the table created by hand.

### C.2 The fix

Revision **`f687a630a8d1`** `add_relationship_timeline_entries` (down-revision `e7a4c2d9f1b3`; single head). It creates the table exactly as the model declares it: 12 columns (UUID primary key defaulting to `gen_random_uuid()`, FK `session_id` to `sessions.id`, `entry_type`, `title`, `description`, `content`, `turn_id`, `status` defaulting to `'active'`, JSON `tags` and `metadata`, timezone-aware `created_at`/`updated_at` defaulting to `now()`), the two named check constraints for `entry_type` and `status`, and indexes on `session_id`, `entry_type`, `turn_id` and `status`. Alembic's own autogenerate comparison of the live table with the model reports no differences.

- **Existing tables:** if the table is already present (created by hand or from the model), nothing is dropped or rewritten. A compatible table is adopted (missing indexes are added); an incompatible one stops the upgrade with an error listing the differences and is left untouched.
- **Foreign key:** `ON DELETE CASCADE`, in the migration **and** in the model so they match. With a plain foreign key, deleting any session that has a timeline entry failed (verified on PostgreSQL); session deletion owns its conversation rows. This is the only change to the model (schema metadata).
- **Downgrade:** drops the table and its indexes (and any rows in it); it was exercised only on a throwaway database.

### C.3 A second defect found and fixed with it

The standalone `RelationshipTimeline.add_timeline_entry/update_timeline_entry/resolve_entry` opened their own session and then called a helper that **flushed instead of committing** whenever `in_transaction()` was true. `Session.add()` already begins a transaction, so every standalone write was rolled back when the session closed (confirmed on PostgreSQL: 0 rows persisted). The helper is now `_commit_owned_session`. The turn path added in Task 0.3 (entries joined to the turn's transaction) was unaffected. `chatbot/continuity/rituals.py` contains the same pattern and was not changed.

### C.4 Verification

A brand-new `pgvector/pgvector:pg16` container (RAM-backed storage, no volumes, port 55432) was built to 30 tables with `alembic upgrade head` alone; no table was created manually. The development volume `chatbot_postgres-data` and development services were never touched, and the container was removed afterward.

| Suite | Result |
|---|---|
| `tests/test_timeline_migration.py` (9: revision chain, schema match, insert/read-back, turn-session commit/rollback, constraints, session-delete cascade, downgrade/upgrade, adoption, incompatible-table refusal) | 9/9 pass on PostgreSQL; the chain check also runs without a database |
| `tests/test_completion_persistence.py` | 19/19 pass on PostgreSQL, timeline included, without manual table creation |
| `tests/test_conversation_lifecycle.py` (including the PostgreSQL characterization test) | 11/11 pass |
| Default environment, targeted set | 215 passed, 9 PostgreSQL-only skipped |
| Wider non-integration suite | failure set identical to the pre-Phase-0 baseline (42 failed, 56 errors, all pre-existing) |
| Mutation checks | a no-op migration fails 7 of the 9 migration tests; removing the adoption index step fails the adoption test |

The migration tests run only when `PHASE0_PG_URL` names a disposable instance (database `phase0_accept`, non-default port); each test creates and drops its own database.

### C.5 Phase 0 status

All planned Phase 0 work is implemented and verified, and no schema defect is known. Open items are product decisions (Addenda A.4 and B.4), not defects. Owner acceptance for integration is recorded on 2026-11-07, based on the completed implementation and reported acceptance evidence above. **Phase 0: ACCEPTED.** Integration tests on main are recorded separately in the integration report; the live end-to-end spoken latency was not re-measured. The preexisting standalone persistence path in `rituals.py` still has a flush-without-commit defect and remains a follow-up outside this task. Phase 1 is not started.


## Evidence legend

| Tag | Meaning |
|---|---|
| **[V-code]** | Verified by reading the current source (file:line given). |
| **[V-test]** | Verified by running an existing test (baseline in §12.1). |
| **[Static]** | Follows from code plus library semantics, **not** confirmed at runtime. PostgreSQL is not running on this machine (`pg_isready`: no response), so DB-transaction findings are static until the characterization test in §12.3 (T0) is run. |

Line numbers refer to the tree at commit `bebb452` (clean source tree; only the three new planning docs are untracked).

---

## 1. Executive Recommendation

### 1.1 Verified root cause of conversation-path divergence

The previous reports describe "three implementations". The code is more specific, and the difference matters for the fix:

1. **There is already one step list.** `build_default_pipeline()` (`core/turn.py:1353`) builds a 14-step `TurnPipeline`. Standard chat runs it through `execute_turn()` (`chat.py:265-271`). Streaming iterates the *same* `pipeline._steps` by hand (`streaming.py:245`), skipping `update_identity` and replacing `call_provider`. **Spoken chat is not a third implementation:** `_spoken_turn` calls `stream_chat()` directly with a synthetic `Request` (`avatar/router.py:85-121`), so spoken ≡ streaming by construction.
2. **Divergence lives in the code around the step list**, in four places:
   - **Prelude** (what goes into `TurnContext`): `chat.py:96-193` vs `streaming.py:79-239`. The two build different `persona`, session-state, room, and metadata inputs.
   - **Provider call**: `CallProviderStep.run` (`turn.py:633-664`) vs `_stream_provider_tokens` (`streaming.py:496-566`). Only the standard path applies the per-model prompt formatter.
   - **Step driver**: `TurnPipeline._run_steps` + intent routing (`turn.py:1158-1316`) vs the streaming loop (`streaming.py:245-344`). The error, trace, and routing behavior differ.
   - **Epilogue** (persistence and post-turn effects): one request-scoped transaction committed at `chat.py:226`, vs separate sessions plus an explicit `stream_db.rollback()` at `streaming.py:355`.
3. **Two of the differences are correctness defects rather than design choices** (§4): in streaming/spoken the in-loop writes are discarded by that rollback **[Static]**, and the system prompt text is different between standard and streaming for the same companion **[V-code]**.

### 1.2 Recommended architecture: one engine, thin delivery adapters, no new framework

Keep `TurnContext` and `TurnPipeline` as the engine. Add three things and delete the duplicates they replace:

| Addition | Where | Replaces |
|---|---|---|
| **Shared conversation module** holding the one implementation of state loading, context building, memory-workflow prep, message persistence, effects runner and title scheduling | new `chatbot/api/conversation.py` (relocated code, not new logic) | ~15 duplicated or near-duplicated helpers in `chat.py` and `streaming.py` (§3) |
| **Shared provider-call preparation**: `ProviderInputs` + `prepare_provider_call(ctx)` | `core/turn.py`, next to `_provider_history` / `_formatted_provider_inputs` | The two divergent provider-input builders |
| **Step groups and an async step iterator** (`PREPARE`, `GENERATE`, `COMPLETION`) over the existing step objects, plus `TurnOutcome` | `core/turn.py` | The hand-written streaming loop; `execute_turn` fast-path routing |

`chat.py` and `streaming.py` shrink to **delivery adapters** (HTTP/auth edge, JSON vs SSE, avatar coordination). `avatar/router.py` and `avatar/coordinator.py` are **not modified**.

### 1.3 Headline findings that shape the plan

| # | Finding | Evidence |
|---|---|---|
| F1 | Streaming/spoken run `extract_memory`, `update_mood`, `persist_emotional_state` in-loop on `stream_db`, then `stream_db.rollback()` runs before any commit. Extracted facts/episodes, `bot_mood_logs`/`user_state_logs`, and pre-turn embedding writes are discarded. Timeline entries use their own session and **do** persist. | `streaming.py:199,231,326,354-361`; `extraction.py:182-195`; `turn.py:4029-4044` **[Static]** |
| F2 | The system prompt differs by mode for the same companion: standard passes `DEFAULT_PERSONALITY` as `persona`; streaming passes `{}`. | `chat.py:156,805-822` vs `streaming.py:214`; `persona_prompt.py:19,156-166` **[V-code]** |
| F3 | Standard chat never reads persisted mood/user-state/mode (`sessions_store` is never written; no `session_record`), so mood resets to default every turn. Streaming reads it from `session_record`. | `sessions.py:59` (no `sessions_store[` writes anywhere); `chat.py:132`; `turn.py:3656-3672`; `streaming.py:197` **[V-code]** |
| F4 | Standard chat routes greeting/casual/feedback turns through a "fast path" (no mood/mode/user-state, deferred extraction); streaming never does. Prompt content and effects differ by mode. | `turn.py:173-192,1158-1226` **[V-code]** |
| F5 | Streaming sends *unformatted* provider inputs; standard sends `format_prompt_for_model` output. | `streaming.py:517-520` vs `turn.py:648-651,3380-3415` **[V-code]** |
| F6 | Several "capabilities" described as active are not wired in any mode (§4.3): relationship config, linguistic fingerprint, token budgeting, `ContextCompiler`. These are **not** mode divergences, so Phase 0 must not wire them (that would change prompts). They get named integration points. | §4.3 **[V-code]** |

### 1.4 What this design does *not* do

No schema changes, no new dependencies, no new LLM or embedding requests, no new background infrastructure, no change to retrieval ranking or the `<->` operator, no avatar media or connection-ownership change, no Phase 1+ capability. Behavioral changes that follow from consolidation are listed explicitly as **decisions D1–D14** (§16). None is implemented silently.

### 1.5 Plan in one paragraph

Task **0.2** unifies preparation (loaders, context builder, provider inputs, step groups). Task **0.3** unifies completion (outcomes, persistence, effects isolation, embeddings, cancellation). They touch the same four files, so they run **sequentially**. A test-harness task (**0.4**, tests only) can run in parallel with 0.2 once the contracts in §6–§7 are fixed. Details in §13–§15.

---

## 2. Existing Conversation Paths

### 2.1 Common step list (`build_default_pipeline`, `core/turn.py:1375-1395`)

| # | Step `name` | Class (line) | Network? | Writes DB? |
|---|---|---|---|---|
| 1 | `receive` | `ReceiveStep` 303 | no | no |
| 2 | `room_context` | `RoomContextStep` 896 | no (own DB via `get_room`) | no |
| 3 | `retrieve` | `SceneAwareRetrievalStep` 940 / `RetrieveStep` 311 | yes: 1 query embedding + vector search | no |
| 4 | `infer_user_state` | `InferUserStateStep` 403 | no (heuristics) | no |
| 5 | `select_conversation_mode` | `SelectConversationModeStep` 462 | no | no |
| 6 | `compile_context` | `CompileContextStep` 510 | no | no (may read profile/identity) |
| 7 | `call_provider` | `CallProviderStep` 630 | **LLM** | no |
| 8 | `update_mood` | `UpdateMoodStep` 667 | no | no |
| 9 | `persist_emotional_state` | `PersistEmotionalStateStep` 713 | no | `sessions` row, `bot_mood_logs`, `user_state_logs` |
| 10 | `extract_memory` | `ExtractMemoryStep` 731 | no (regex) + per-message embed (741/754/764/798) | `semantic_facts`, `episodes` |
| 11 | `scene_memory_gate` | `SceneMemoryGateStep` 967 | no | no |
| 12 | `timeline_update` | `TimelineUpdateStep` 802 | no | `timeline_entries`, rituals (own sessions) |
| 13 | `respond` | `RespondStep` 849 | no | no (sets `final_response`) |
| 14 | `update_identity` | `UpdateIdentityStep` 859 | no | `identity_states`, `sessions.mood_state` |

### 2.2 A. Standard chat — `POST /chat/` (`chat.py:81-242`)

```
chat()                                              chat.py:82
 ├─ Session lookup, 404 (:96-104)
 ├─ CompanionManager.get + companion-mismatch 400 (:106-120)        [standard-only validation]
 ├─ _build_companion_character_profile (:123,704)  → _companion_to_profile_dict (:737)
 ├─ _load_session_identity_state (:130,748)        → ORM row
 ├─ sessions_store.get → {} in practice (:132)
 ├─ _load_chat_room (:133,528) → _validate_room_session (:571)
 ├─ _load_db_session_history (:141,491)  full session history, unbounded
 ├─ _build_room_system_prompt (:149,587) / _inject_room_system_prompt (:155,630)
 ├─ _load_persona (:156,765) → DEFAULT_PERSONALITY dict (:805)
 ├─ _build_metadata (:165,274) + apply_routing_overrides (:166) + thinking (:167)
 ├─ TurnContext(...) + metadata keys (:169-193)
 ├─ _prepare_memory_workflow (:195,326) → _generate_recent_message_embeddings (:336,350)
 │     (ONE batched embed of ≤20 un-embedded messages; flush only)
 ├─ _run_pipeline (:196,265) → build_default_pipeline() [per call] → execute_turn(update_identity=True)
 │     execute_turn (turn.py:1158): _route_by_intent → fast | deep; background_queue present
 │       fast & no room: FAST_PATH_STEPS + enqueue_deferred (BackgroundQueue memory_extract)
 │       else: all 14 steps through _run_steps (:1252) [step exception → pipeline_error, return ctx]
 ├─ Persist user+assistant Message rows via request `db` (:201-224); db.commit() (:226)
 ├─ _maybe_schedule_title_generation (:228,903)   [dormant: sessions are created titled "New Chat", sessions.py:84]
 └─ ChatResponse (:237)
```

### 2.3 B. Streaming chat — `POST /chat/stream` (`streaming.py:79-135`, `_stream_turn` 138-493)

```
stream_chat()                                       streaming.py:80
 ├─ Session lookup (room filter on Session.room_id when request.room_id) (:95-105)
 ├─ _load_stream_companion_profile (:107,890)  [session-bound companion only]
 ├─ _load_stream_session_identity (:112,920)   → dict
 ├─ _load_db_session_history (:113,720)        [different room predicate than chat.py]
 ├─ _session_snapshot (:118,867)
 └─ ChatStreamingResponse(_stream_turn(...))   (:120-135)
_stream_turn()                                      streaming.py:138
 ├─ stream_db = streaming_session_factory() (:153)  [NullPool engine, db/session.py:19-25]
 ├─ avatar bound? coordinator.begin(...) (:162-177): interrupts prior turn, waits for its persistence
 │     avatar: RELOAD history + identity AFTER begin (:182-185)   [2nd load of both]
 ├─ _stream_metadata (:188,657) + routing + thinking + turn_id (:189-191)
 ├─ metadata: session_record(snapshot), db=db_session=stream_db, user/companion/room (:196-202)
 ├─ _seed_stream_history_metadata (:204,690); TurnContext(persona={}) (:210-222)
 ├─ _prepare_memory_workflow (imported from chat.py) (:231)
 ├─ build_default_pipeline() [no persona_cache, no background_queue] (:235); _start_turn_trace (:237)
 ├─ SSE typing/started (:241)
 ├─ for step in pipeline._steps (:245):  skip update_identity
 │     call_provider → _stream_provider_tokens (:261,496) [no formatter]; publish to coordinator
 │        spoken: wait_playback (≤180 s) before continuing (:301-310)
 │     other steps: step.run(ctx) with SSE step events (:323-344); [step exception propagates]
 ├─ stream_db.rollback() (:354-361)   ◄── discards uncommitted in-loop writes (F1)
 ├─ _persist_messages (:363-389)  fresh session, own commit; strict iff avatar-bound
 ├─ title scheduling only when NOT avatar-bound (:390-399)
 ├─ _run_post_stream_db_write(_generate_message_embeddings) (:401)  per-message, sequential
 ├─ SSE `response` event (:407)
 ├─ _run_post_stream_db_write(UpdateIdentityStep) (:409)
 ├─ _complete_turn_trace (:415); `[DONE]` (:416)
 └─ handlers: Cancelled/GeneratorExit (:417-464), HTTPException (:465), Exception (:469-484); finally (:485-493)
```

### 2.4 C. Spoken chat — `/api/avatar/ws` (`avatar/router.py`)

```
avatar_websocket (:149): auth → _authorize (:70) → AvatarBinding → coordinator.connect → receive loop
STT transcript event → coordinator.emit (coordinator.py:360) → asyncio.create_task(binding.on_transcript)
_spoken_turn (:85):
   Request(scope) with state.avatar_source="spoken", avatar_binding=binding
   stream_chat(http_request, StreamRequest(session_id, message, provider_override, model_override, thinking), user, db)  (:109)
   iterate response.body_iterator, parse each SSE frame, forward JSON to the browser (:121-137)
→ identical to path B with avatar_turn.source == "spoken":
   * 409 if avatar missing/replaced/companion changed or character_profile is None (streaming.py:166-172)
   * wait_playback before post-generation steps (:301-310)
   * persistence strict (:370); interruption persists only HEARD text + system marker (:436-457)
```

### 2.5 Typed turn with an attached avatar

`source == "typed"` with a bound avatar (`streaming.py:164-177`): text is published to the coordinator, speech plays, the turn does **not** wait for playback, and a browser SSE close after persistence is not an interruption (`:420-435`). Covered by `test_typed_speech_survives_the_browser_closing_sse_after_the_full_response`.

---

## 3. Duplicated Business Logic Inventory

Legend for **Modes**: S = standard, T = streaming typed, P = spoken. "Authoritative" = the implementation that survives (§9).

| # | Operation | Existing implementations (file:line) | Modes | Duplicated? | Behavioral difference | Reusable existing function | Change | Unchanged |
|---|---|---|---|---|---|---|---|---|
| 1 | Session ownership lookup | `chat.py:96-104`; `streaming.py:95-105`; `router._authorize` 70-82 | S / T,P / P | partly (HTTP vs WS auth is delivery) | streaming adds `room_id` filter | — | keep at edges; share nothing | WS auth |
| 2 | Companion profile load | `_build_companion_character_profile` `chat.py:704`; `_load_stream_companion_profile` `streaming.py:890`; `_companion_to_profile_dict` ×2 (`chat.py:737`, `streaming.py:909`) | all | **yes (verbatim)** | standard accepts body `companion_id` + mismatch 400 | `CompanionManager.get` `companion.py:360` | one `load_conversation_state` | `CompanionManager` |
| 3 | Identity state load | `_load_session_identity_state` `chat.py:748` (ORM row); `_load_stream_session_identity` `streaming.py:920` (dict); avatar re-load `streaming.py:185` | all | **yes** | return type differs; coerced identically by `_coerce_identity_state` `turn.py:2774` | `_coerce_identity_state` | one loader returning the dict | `turn.py` coercion |
| 4 | Persona dict | `_load_persona` `chat.py:765` → `DEFAULT_PERSONALITY`; streaming `persona={}` `streaming.py:214` | S vs T,P | **yes (diverged)** | **F2** | — | one `resolve_persona` (D1) | `PersonaPromptAssembler` |
| 5 | Session emotional state input | standard: none (metadata `session` is `{}`); streaming: `session_snapshot` → `session_record` | S vs T,P | **yes (diverged)** | **F3** | `_read_session_field` `turn.py:3656` | one snapshot (D3) | `turn.py` readers |
| 6 | Room resolution | `_load_chat_room` `chat.py:528` (body `room_id`, session room, legacy `Room.session_id` fallback); streaming: `Session.room_id` only | S vs T,P | yes | body `room_id` honored/validated only in standard. `link_room_session` sets both sides (`rooms/room.py:274-275`), so linked sessions match | `RoomContextStep` | one loader (superset semantics) | `RoomContextStep` |
| 7 | Room type prompt | `_build_room_system_prompt` `chat.py:587`, `_inject_room_system_prompt` `chat.py:630`; streaming none | S only | n/a | Injected into `history`, then dropped by `RetrieveStep` once the loader returns user/assistant rows (`working_memory.py:256`; `turn.py:342`); `metadata["room_system_prompt"]` has **no consumer** (grep) → reaches the model only on a room's first turn, standard only | `compose_prompt_layers(room_scene=…)` | **D6** | `render_room_prompt` |
| 8 | Conversation history load | `_load_db_session_history` `chat.py:491` and `streaming.py:720` | all | **yes** | different room predicate (`Session.room_id == r OR Room.session_id link` vs `Session.room_id == r` only); unbounded query in both | — | one function | `WorkingMemoryLoader` (20 turns / ~4000 tokens, user+assistant only) |
| 9 | Working-memory window | `RetrieveStep` → `WorkingMemoryLoader.load` `turn.py:322-349`, `working_memory.py:99-143` | all | no | none | same | none | **unchanged** |
| 10 | Context metadata assembly | `_build_metadata` `chat.py:274`; `_stream_metadata` `streaming.py:657`; `_seed_stream_history_metadata` `streaming.py:690`; inline blocks `chat.py:169-193`, `streaming.py:196-226`; `_first_state_value` ×2 | all | **yes** | different key sets (`session`/`session_store`/`persona` vs `session_record`/`db_session`) | — | one `build_turn_context` | — |
| 11 | Embedding batch + retrieval wiring | `_prepare_memory_workflow` `chat.py:326`; streaming imports it (`:23`) | all | no (already shared) | batch writes are `flush`ed only (see F1) | same | move; commit-on-write (D12) | `RetrievalEngine`, `SessionAwareVectorStore` |
| 12 | Retrieval | `RetrieveStep` / `SceneAwareRetrievalStep`; `app.state.retrieval_engine` (`app.py:48`, own session factory) | all | no | none | same | none | **unchanged** |
| 13 | Intent routing / fast path | `_route_by_intent` `turn.py:1194`; `FAST_PATH_STEPS` `:173`; used only by `execute_turn` | S only | n/a | **F4** | — | retire (D2) | — |
| 14 | Context compilation + persona prompt | `CompileContextStep` `turn.py:536-627`; `_compile_context_text` `:3164` | all | no | content differs only through inputs 4, 5, 7, 13 | same | none in the step; inputs unified | **unchanged** |
| 15 | Provider inputs (system + history + formatter) | `CallProviderStep.run` `turn.py:648-651`; `_stream_provider_tokens` `streaming.py:517-520` | S vs T,P | **yes** | **F5** | `_provider_history` `turn.py:3364`, `_formatted_provider_inputs` `:3380` | `prepare_provider_call` | both helpers |
| 16 | Provider selection / config / errors | `_select_provider` `:3253`, `_provider_config` `:3328`, `ProviderSelectionError` handling in both | all | partly | same text/outcome | same | fold into `prepare_provider_call` | selection logic |
| 17 | Step driving, trace, errors | `TurnPipeline._run_steps` `turn.py:1252`; streaming loop `streaming.py:245-344` | S vs T,P | **yes** | step exception: standard → `pipeline_error` string persisted as assistant text with HTTP 200 (`chat.py:197-199`); streaming → error event, **nothing persisted** (`streaming.py:469-484`) | `_start_turn_trace`/`_record_turn_trace_step`/`_complete_turn_trace` | step iterator | trace helpers |
| 18 | Message persistence | inline `chat.py:201-226`; `_persist_messages` `streaming.py:746-822` | all | **yes** | timestamps/ordering logic duplicated; standard persists raw `body.message`, streaming the stripped one; standard atomic with effects, streaming separate tx; `strict` only in streaming/avatar | — | one `persist_turn_messages` | `Message` model |
| 19 | Post-turn effects (mood, extraction, timeline, identity) | in-pipeline (S); in-loop + post-stream (T,P) | all | **yes (same steps, different tx/ordering)** | **F1**; identity timing differs | `ExtractMemoryStep`, `UpdateMoodStep`, … | one effects runner | step classes |
| 20 | Message embeddings after turn | `ExtractMemoryStep` calls `_generate_message_embeddings` ×4 (`turn.py:741,754,764,798`, no-op for the current turn in standard: rows not yet persisted); streaming post-persist call `streaming.py:401` (2 sequential calls) | S vs T,P | **yes** | S relies on next-turn batch; T,P embed post-persist per message | `_generate_recent_message_embeddings` (batch) | one batch helper (D12) | `OpenRouterEmbeddingProvider` |
| 21 | Title generation | `_maybe_schedule_title_generation` + `_generate_conversation_title` in both files (~140 lines duplicated) | S, T (not P / not avatar-bound) | **yes (verbatim)** | skipped when avatar-bound | — | one copy | — |
| 22 | Cancellation / interruption persistence | `streaming.py:417-464` | T,P | no | S has no cancellation handling | — | folded into outcome handling | `AvatarCoordinator` |
| 23 | Trace / prompt inspection | `_store_llm_trace_metadata` `turn.py:1656`, `_trace_prompt` `:1749`, `_render_trace_prompt` `:1760` | all | no | `llm_prompt` is the formatted prompt in S, unformatted in T,P (F5) | same | unchanged | **unchanged** |
| 24 | Identity evolution (random drift) | `UpdateIdentityStep` `turn.py:859` → `evolution.calculate_drift` (`random.uniform`, `evolution.py:188`) | all | no | S in-pipeline; T,P post-persist, skipped on interruption | same | keep as slot in completion group | **unchanged** (Phase 3 replaces) |
| 25 | Relationship state | `RelationshipConfigModel` read/written only by `api/config.py` (`:114-136`); **never** passed to `assemble()` | none | n/a | none (dead input in every mode) | `_persona_identity_inputs` `turn.py:2638` | integration point only | — |

**Functions that can remain unchanged:** `RetrieveStep`/`SceneAwareRetrievalStep`, `WorkingMemoryLoader`, `RetrievalEngine`, `SessionAwareVectorStore`, `CompileContextStep` body, `PersonaPromptAssembler`, `compose_prompt_layers`, `_compile_context_text`, `_provider_config`, `_select_provider`, `_store_llm_trace_metadata`, trace helpers, all `AvatarCoordinator`/`AvatarTurn` code, `MemoryExtractor`, `RoomContextStep`, `SceneMemoryGateStep`, `TimelineUpdateStep`, `UpdateIdentityStep` body.

---

## 4. Confirmed Behavioral Differences

### 4.1 Mode-to-mode differences (observable)

| ID | Difference | S | T/P | Evidence | Impact |
|---|---|---|---|---|---|
| **C1** | System-prompt persona block | `DEFAULT_PERSONALITY` → styled "Persona / Identity: Companion / Tone: neutral / Traits: empathetic, analytical, clear / Backstory: A helpful, thoughtful chatbot companion." plus identity sections | `{}` → fallback "You are a helpful, neutral assistant…" plus identity sections | `chat.py:156,805`; `streaming.py:214`; `persona_prompt.py:19-22,156-166,175-205`; `personality.py:25-31` | **High.** Different compiled prompt for the same companion. Both are generic-assistant text, exactly the drift the roadmap targets. |
| **C2** | Persisted mood / user_state / conversation_mode as prompt input | not read (resets to default each turn) | read from `session_record` | `chat.py:132` + `sessions.py:59`; `turn.py:3568-3672`; `streaming.py:197` | **High** for P03 (emotional continuity). |
| **C3** | Intent routing | greeting/casual/feedback and no room → fast path: skips `infer_user_state`, `select_conversation_mode`, `update_mood`, `persist_emotional_state`, `extract_memory`, `timeline_update`; extraction deferred to in-memory `BackgroundQueue` | always the full list | `turn.py:173-192,1158-1226` | **High.** Prompt and effects differ by mode. |
| **C4** | Provider input formatting | `_formatted_provider_inputs` (system lifted/merged, empty and whitespace-only content dropped, content stripped; sets `llm_prompt_payload`, `llm_prompt_family`, `llm_prompt_formatter`) | raw `_provider_history` + raw `user_message` | `turn.py:648-651,3380-3415`; `streaming.py:517-520` | Medium. Provider payload and trace differ slightly; inspection metadata missing for streamed turns. |
| **C5** | Fate of in-loop/in-pipeline DB writes (facts, episodes, mood logs, user_state, conversation_mode, pre-turn embeddings) | committed with the messages at `chat.py:226` | discarded by `stream_db.rollback()` | `streaming.py:199,231,354-361`; `extraction.py:182-195` (SAVEPOINT via `begin_nested`, never committed); `turn.py:4029-4044` (flush-only inside a transaction) **[Static]** | **High.** Memory/mood effects are silently absent for streaming/spoken. Timeline/rituals (own session factory, `timeline.py:337`) persist in both. |
| **C6** | Post-turn embeddings | none for the current turn; stragglers embedded by next turn's batch (~one request per turn) | 2 sequential per-message requests after persistence, before `[DONE]` | `turn.py:2185-2214`; `streaming.py:401-405`; `embedding.py:15-74` | Medium. Different request counts and timing. |
| **C7** | Step-failure outcome | exception caught → `pipeline_error` text persisted as the assistant message, HTTP 200 | exception → generic error event; nothing persisted | `turn.py:1274-1295`, `chat.py:197-199`; `streaming.py:469-484` | Medium (data and UX differ). |
| **C8** | Disconnect with no avatar, before persistence | n/a (request/response) | nothing persisted (user message lost) | `streaming.py:417-436` (the `avatar_turn is None` case re-raises with no persistence) | Medium; decision D8. |
| **C9** | Identity update timing/transaction | inside the pipeline, same commit as messages | after persistence, separate session; skipped on interruption and on no-avatar typed disconnect | `chat.py:268,226`; `streaming.py:409-413` | Medium. |
| **C10** | Room type prompt, body `room_id` validation, legacy `Room.session_id` fallback | yes (room prompt effectively first turn only; see §3 #7) | no | `chat.py:133,149-155,528-555` | Low-Medium. |
| **C11** | Companion validation | body `companion_id` accepted, mismatch → 400 | session-bound only | `chat.py:106-120` | Low (API surface; keep as an HTTP-edge parameter). |
| **C12** | History room predicate | `Session.room_id == r OR Session.id ∈ Room.session_id` | `Session.room_id == r` | `chat.py:501-514`; `streaming.py:727-730` | Low (equivalent for sessions linked via `link_room_session`). |
| **C13** | Title generation | scheduled (dormant: sessions created titled "New Chat") | scheduled only when no avatar bound | `chat.py:228`; `streaming.py:390-399`; `sessions.py:84` | Low. |
| **C14** | Persisted user text | raw `body.message` | stripped (`StreamRequest.str_strip_whitespace`) | `chat.py:61-68,209`; `streaming.py:58` | Low. |
| **C15** | Spoken-only precondition | none | 409 if avatar missing/replaced/companion changed or `character_profile is None` | `streaming.py:166-172` | Keep (delivery-specific). |
| **C16** | `session_snapshot` freshness | n/a | for avatar-bound turns the snapshot is taken **before** `coordinator.begin` waits for the previous turn's persistence; identity is re-read after `begin` (`:185`), the snapshot is not | `streaming.py:118,176-187` | Low-Medium (stale mood). |

### 4.2 Claims in earlier reports, checked against code

| Claim | Verdict |
|---|---|
| Standard chat runs `TurnPipeline.execute_turn()` | **True** (`chat.py:268`). |
| Streaming runs "selected steps through a separate implementation" | **True but narrower:** it iterates the *same* `_steps` (`streaming.py:245`). |
| Spoken uses the streaming path | **True** (`router.py:109`). |
| Regex extraction/timeline are skipped in SSE/spoken | **Partly.** The steps *run*; extracted facts/episodes and emotional logs are rolled back (C5). Timeline persists. |
| Audit §8: "three paths share identical identity, memory, retrieval, embedding" | **False in detail** (C1–C4). Retrieval and the working-memory window *are* shared. |
| Audit: retrieval uses cosine `<->` | **Wrong.** `<->` is pgvector's L2 distance (`pgvector_store.py:55,59`); retrieval treats it as a distance (`1/(1+d)`). Do not change in Phase 0 (D10; Task 2.4 owns it). |
| Audit: linguistic fingerprint ACTIVE | **False in live paths.** `_companion_to_profile_dict` (both copies) carries 6 fields; `_assemble_persona_prompt` (`turn.py:3060`) passes no fingerprint kwargs. The fields exist on `Companion` (`companion.py:38-40,86-88`) but never reach the prompt. |
| Audit §6: "Relationship Config & Tone Modifiers" in the system prompt | **False.** `relationship_config` is never loaded. |
| Audit: `ContextCompiler` unused | **True.** |
| Audit: consolidation not triggered for streaming/spoken | **True, and stronger:** never triggered for standard either. `execute_turn` is driven by a fresh `TurnPipeline` per request (`chat.py:267`), so `_session_turn_counts` (`turn.py:1150,1233-1249`) never reaches the threshold of 5. |
| Audit: embedding batching "in chat.py and streaming.py" | **True** (shared function), but its writes are only `flush`ed (C5). |

### 4.3 Behavior common to all modes (not divergence; do **not** change in Phase 0)

| Observation | Evidence | Disposition |
|---|---|---|
| Conversation history is sent twice per turn: flattened inside the system message (`compiled_context` → "Conversation history:") **and** as role messages from `_provider_history` | `turn.py:3164-3184,3364-3377` | D9: decide in Task 1.3 (prompt change). |
| No token budgeting applies. `compose_prompt_layers` is called without `token_budget`; the only cap is the loader's 20 turns / 4000 estimated tokens | `turn.py:554-558`; `prompt_layers.py:303,373`; `working_memory.py:104-106` | Record as a decision field; do not apply (changes prompts). |
| `ContextCompiler` (`memory/context_compiler.py`) is dead code | grep: no callers | Leave; `_compile_context_text` is authoritative. |
| Retrieval is per-session and Message-based; semantic facts are not retrieved | `retrieval.py:135-200`, `pgvector_store.py:45-100` | Phase 2. |
| Retrieval engine uses its **own** session factory, so uncommitted pre-turn embedding writes are invisible to the same turn's retrieval | `app.py:48`; `retrieval.py:795` | Informs D12. |
| Full session history is loaded (unbounded) every turn and also feeds `full_conversation_history` for identity drift | `chat.py:491`; `turn.py:1259,2965` | Optional follow-up (§11.3); drift depends on it. |
| Title generation is dormant | `sessions.py:84` | Dedupe only (D13). |
| `ctx.metadata["mood_state"]` is written by three different producers (mood engine, user-state step, and `UpdateIdentityStep` with the evolution mood dict) | `turn.py:456,709,889` | Risk R7 (Phase 1/5 problem). |

---

## 5. Proposed Shared Conversation Lifecycle

### 5.1 Shape

```
 [HTTP /chat/]        [HTTP /chat/stream]        [WS /api/avatar/ws → stream_chat]
      │ auth, 404/400/409         │ auth, 404/409, avatar begin()          │ (unchanged wrapper)
      └───────────────┬───────────┴────────────────────────────────────────┘
                      ▼
   ┌──────────────────────────────── PREPARE (one implementation) ────────────────────────────┐
   │ load_conversation_state → build_turn_context → prepare_memory_workflow                    │
   │ → pipeline.iter_steps(PREPARE_STEPS)  [receive, room_context, retrieve, infer_user_state, │
   │                                         select_conversation_mode, compile_context]        │
   └───────────────────────────────────────────────────────────────────────────────────────────┘
                      ▼
   ┌────────────── GENERATE (one provider-call preparation; two call styles) ──────────────────┐
   │ prepare_provider_call(ctx) → ProviderInputs                                               │
   │   standard: provider.complete(...)      stream/spoken: provider.stream(...) → tokens       │
   │ then step `respond` (sets final_response)                                                  │
   └───────────────────────────────────────────────────────────────────────────────────────────┘
                      ▼ outcome ∈ {completed, failed, interrupted, abandoned}
   ┌─────────────────────── COMPLETE (one implementation, two entry points) ──────────────────┐
   │ persist_turn(...)  → PersistedTurn   (own session, idempotent, strict per delivery)       │
   │   [adapter emits `response` / returns JSON here]                                           │
   │ run_turn_effects(...) → effects group in ONE isolated session, SAVEPOINT per step,        │
   │   eligibility from EFFECT_POLICY[outcome]; trace completion                               │
   └───────────────────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 What stays delivery-specific (and only this)

| Concern | STANDARD | STREAMING | SPOKEN |
|---|---|---|---|
| Entry/auth | `POST /chat/`, 404/400 | `POST /chat/stream`, 404 | WS auth, `_authorize`, 409 on avatar drift |
| Output | one JSON `ChatResponse` | SSE `typing`/`step`/`context`/`token`/`response`/`error`/`[DONE]` | same SSE frames, forwarded to the WS by `_spoken_turn` |
| Generation call | `provider.complete` | `provider.stream` (no buffering) | same; plus `coordinator.publish` per token |
| Turn id | `uuid4` | `uuid4` | `avatar_turn.turn_id` |
| Completion trigger | after `complete()` | after last token | after last token **and** `wait_playback` (heard in full) |
| Interruption/cancel | none | client disconnect (D8) | barge-in, disconnect, speech failure, takeover → `INTERRUPTED` |
| Persistence strictness | strict (raise → HTTP 500) | non-strict (log) | strict (blocks next turn on failure; unchanged) |
| Title scheduling | yes | yes (no avatar) | no |
| Post-persist embeddings | none (next-turn batch) | one batch call | one batch call |

These are expressed once, as a frozen `TurnDelivery` policy value (§7.6), not as scattered `if avatar_turn` branches.

### 5.3 Where the code lives (smallest production-quality shape)

| Location | Content | New or existing |
|---|---|---|
| `core/turn.py` | `ProviderInputs`, `prepare_provider_call`, `PREPARE_STEPS` / `GENERATE_STEPS` / `COMPLETION_STEPS`, `TurnPipeline.iter_steps`, `TurnOutcome`, `EFFECT_POLICY`, `CompletedTurn` | existing file, ~150 lines added, ~110 removed (fast path) |
| `api/conversation.py` | `load_conversation_state`, `resolve_persona`, `build_turn_context`, `new_turn_id`, `prepare_memory_workflow` (+ embedding batch), `persist_turn`, `run_turn_effects`, `TurnDelivery`, `schedule_title_generation`, `build_pipeline` | **new file, but almost entirely relocated code.** Justification: `streaming.py` already imports a private helper from the `chat.py` router module (`streaming.py:23`); a neutral module avoids a router importing another router's privates and keeps `core/` free of FastAPI/`HTTPException`. Fallback if rejected (D11): keep these in `chat.py` and import them from `streaming.py`. |
| `api/chat.py`, `api/streaming.py` | delivery adapters only | existing, shrink |
| `avatar/*` | none | **untouched** |

### 5.4 Authoritative implementation per operation

| Operation | Authoritative implementation after refactor |
|---|---|
| Companion identity / profile | `conversation.load_conversation_state` (single `_companion_to_profile_dict`) → `turn._persona_identity_inputs` → `PersonaPromptAssembler.assemble` |
| Personality (persona dict) | `conversation.resolve_persona` (D1) |
| User/relationship context | `ctx.metadata["session_record"]` snapshot (D3); relationship config: integration point only (§7.7) |
| Session context | `load_conversation_state` (session row, room, snapshot) |
| Conversation history | `conversation._load_history` (one SQL) → `RetrieveStep`/`WorkingMemoryLoader` window |
| Embedding preparation | `conversation.prepare_memory_workflow` (batch, single request) |
| Memory retrieval | `SceneAwareRetrievalStep` + `app.state.retrieval_engine` (unchanged) |
| Context compilation | `CompileContextStep` (unchanged) |
| Prompt construction + formatting | `CompileContextStep` → `prepare_provider_call` (`_provider_history` + `_formatted_provider_inputs`) |
| Token budgeting | none applied today; decision field only (§6.4) |
| Message persistence | `conversation.persist_turn` |
| Post-turn dispatch | `conversation.run_turn_effects` over `COMPLETION_STEPS` filtered by `EFFECT_POLICY` |
| Identity update | `UpdateIdentityStep` (unchanged body) as the last effect |
| Memory processing | `ExtractMemoryStep` (unchanged body) as an effect slot |
| Relationship integration | none yet; `_persona_identity_inputs` is the single extension point |

---

## 6. Shared Preparation Contract

Signatures are **contract sketches for implementers**, not application code.

### 6.1 Input

```text
TurnRequest (conversation.py, frozen dataclass)
  user:                CurrentUser                   # auth already done by the adapter
  db_session_row:      Session (ORM)                 # ownership already verified by the adapter
  message:             str                           # raw; stripped once by ReceiveStep
  requested_companion_id: str | None                 # STANDARD only; validated against session binding
  requested_room_id:   str | None                    # STANDARD body / streaming request.room_id
  provider_override, model_override: str | None
  thinking:            bool
  turn_id:             str                           # new_turn_id() or avatar_turn.turn_id
  source:              "standard" | "typed" | "spoken"   # trace/latency labelling only
  avatar_latency_times: dict[str,float] | None       # existing marks; passed through unchanged
```

### 6.2 Loader output

```text
ConversationState (conversation.py, frozen dataclass)
  session_id: uuid.UUID          user_id: str            companion_id: str | None   # effective: session binding, else requested
  character_profile: dict|None   # the ONE _companion_to_profile_dict (6 fields today; see §7.7 for extension)
  identity_state: dict|None      # detached snapshot (not an ORM row)
  room_id: str|None   room_type: str|None   room_system_prompt: str|None
  session_record: dict           # snapshot (id, user_id, companion_id, room_id, title, mood_state, user_state, conversation_mode, ...)
  history: list[{role,content}]  # the ONE _load_history query (full, unbounded; windowed later by RetrieveStep)
```

Rules: errors raised here are the existing HTTP errors (404/400); room and companion semantics are the superset of today's standard chat (§3 #2, #6). For avatar-bound turns the load happens **once, after** `coordinator.begin()` (ordering required so the previous turn's persistence is visible: `streaming.py:180-187`). The endpoint-level load at `streaming.py:107-118` is removed for the avatar path (one fewer full-history query and one fewer identity query per spoken turn).

### 6.3 Output of preparation

`build_turn_context(request, state, ...)` returns the existing **`TurnContext`**. This is the only context type; no parallel object is introduced. Its metadata contract (single writer; today two):

| Key | Value | Notes |
|---|---|---|
| `session_id` (ctx field) | `str(uuid)` | |
| `persona` (ctx field) | `resolve_persona(...)` | D1 |
| `history` (ctx field) | `state.history` | `RetrieveStep` windows it to 20 turns / ~4000 est. tokens |
| `turn_id`, `user_id`, `companion_id`, `room_id` | from request/state | `room_id` normalized once (`_normalize_room_id`) |
| `room_type`, `room_system_prompt` | from state | prompt delivery per D6 |
| `character_profile`, `identity_state` | from state | dict forms; coerced by `turn.py:2744-2791` |
| `session_record` | snapshot dict | D3: now present in **all** modes |
| `thinking`, `EXPLICIT_PROVIDER_KEY`/`EXPLICIT_MODEL_KEY` | via existing `apply_routing_overrides` | unchanged |
| `provider_registry`, `provider_router`, `provider_config`, `provider_policy`, `provider_name`, `model`, `retrieval_engine`/`memory_retriever`, `memory_storage` | from `app.state` through **one** `_first_state_value` + one key map (today two maps) | |
| `working_memory_store` / `working_memory` | `{session_id: reversed(history)}` seed when app state has none | unchanged behavior |
| `db`, `db_session` | the **preparation** session (read/flush only) | closed by the adapter at the end of preparation (§7.5) |
| `full_conversation_history` | `list(ctx.history)` | set before `RetrieveStep` trims; consumed by `UpdateIdentityStep` |
| `avatar_latency_times` | passthrough | all existing marks preserved (§11.1) |

`prepare_memory_workflow(request, ctx, db, session_id)` keeps today's contract (`chat.py:326-347`): batch-embed up to 20 un-embedded messages **in one request**, resolve and install the retrieval engine, record `embedding_prepare_start/complete`. Change: when it wrote vectors it **commits** them (D12) so streaming no longer loses them.

### 6.4 Provider-ready messages, budget, trace

```text
ProviderInputs (core/turn.py, frozen dataclass / NamedTuple)
  provider: Any | None     config: ProviderConfig
  prompt: str              history: list[{role,content}]
  family: str | None       payload: dict | None      # llm_prompt_family / llm_prompt_payload as today
  error: str | None        # "Provider error: …" when ProviderSelectionError (never substitutes a provider)
prepare_provider_call(ctx) -> ProviderInputs
  = _select_provider + _provider_config + _provider_history + _formatted_provider_inputs
    + sets ctx.metadata["llm_history"], ["llm_prompt"] (today set at turn.py:650-651 and streaming.py:519-520)
```

- `CallProviderStep.run` becomes `prepare_provider_call` + `provider.complete(...)`. `_stream_provider_tokens` becomes `prepare_provider_call` + `provider.stream(...)`. Equivalence is then structural, not tested-in.
- **Token budget decision:** none is applied today (§4.3). Phase 0 records `ctx.metadata["token_budget"] = {"applied": False, "history_turn_cap": 20, "history_token_estimate_cap": 4000}` for inspection only. The decision point for a later change is the `compose_prompt_layers(…, token_budget=…)` call at `turn.py:554-558`.
- **Trace/inspection:** `_start_turn_trace` is called once per turn at the start of preparation (as `_run_steps` and `_stream_turn` do today); `_record_turn_trace_step` per step; `_complete_turn_trace` once in completion. `llm_prompt` is always the formatted prompt (D4), so the inspector shows what the provider received.

### 6.5 Step groups and the iterator (reuses existing step objects)

```text
PREPARE_STEPS    = receive, room_context, retrieve, infer_user_state, select_conversation_mode, compile_context
GENERATE_STEPS   = call_provider, respond                     # `respond` moves up: it only copies provider_response → final_response
COMPLETION_STEPS = update_mood, persist_emotional_state, extract_memory, scene_memory_gate, timeline_update, update_identity
TurnPipeline.iter_steps(ctx, names) -> AsyncIterator[StepEvent]   # one body; _run_steps drains it
```

`iter_steps` is the existing `_run_steps` loop (`turn.py:1252-1316`) refactored to yield after each step, so the SSE adapter can emit its existing per-step events from the same driver rather than a second loop. Moving `respond` earlier is behavior-neutral: it has no dependency on the effect steps, and persistence needs `final_response` before effects run.

### 6.6 Equivalence guarantee

For equivalent state (session row, companion, room, history, retrieval results, routing), `prepare_provider_call(ctx)` yields identical `(prompt, history, config.model/temperature/top_p/thinking)` regardless of delivery. Proven by test A (§12.3).

---

## 7. Shared Completion Contract

### 7.1 Milestones (distinct, per the architecture doc §4)

| Milestone | Definition | Recorded |
|---|---|---|
| *generated* | provider finished (or failed/was cut) | `ctx.provider_response` |
| *delivered* | text left the process (HTTP return / SSE token / TTS segment heard) | `avatar_turn.delivered_text` for spoken; all tokens for typed |
| *persisted* | user+assistant rows committed | `PersistedTurn` |
| *effects scheduled* | `run_turn_effects` entered | `ctx.metadata["turn_effects"]` |
| *effects applied* | each effect step reported applied, skipped (policy) or failed | `turn_effects[step] = applied \| skipped:<reason> \| failed:<err>`; also written to the trace step metadata and one INFO log line `turn_completed …` |

A turn is **complete** when it is *persisted* (and, for spoken, fully *heard*). It is **not** tied to WebSocket liveness or to TTS finishing late; a late AVTR/TTS event after `interrupted` cannot reactivate it (existing `coordinator.emit` guards, `coordinator.py:378-385`, unchanged).

### 7.2 Outcome table

| Outcome | Trigger | User message | Assistant message | Memory/timeline/mood/identity effects | Today |
|---|---|---|---|---|---|
| `COMPLETED` | generation finished; spoken: all segments heard (`wait_playback` returned, or speech skipped) | persisted | full `final_response` | **all eligible** (`EFFECT_POLICY`) | same |
| `FAILED` | provider error string (`"Provider error: …"`), `ProviderSelectionError`, or a step exception | persisted | the error text (legacy, D7) | **legacy default: same as COMPLETED** (see D7 for recommended gating) | standard: yes; streaming: provider error yes / step exception persists nothing |
| `INTERRUPTED` | spoken barge-in, disconnect, takeover, speech failure, playback timeout | persisted | **heard/delivered prefix** only, omitted if empty, plus the existing system marker row | **none** | same (`streaming.py:436-463`) |
| `ABANDONED` | non-avatar typed SSE client disconnects before persistence | **not persisted** (legacy; D8) | not persisted | none | same |

Typed disconnect **after** persistence is `COMPLETED`: the shielded continuation still runs the effects for all modes.

### 7.3 `EFFECT_POLICY` (single table, one place)

| Effect step | COMPLETED | FAILED (default) | INTERRUPTED | ABANDONED | Notes |
|---|---|---|---|---|---|
| `update_mood` | yes | yes (legacy) | no | no | in-memory |
| `persist_emotional_state` | yes | yes (legacy) | no | no | now actually committed in streaming/spoken (C5) |
| `extract_memory` | yes | yes (legacy) | no | no | regex; scene isolation via `scene_memory_gate`; **no backfill** |
| `scene_memory_gate` | yes | yes | no | no | unchanged |
| `timeline_update` | yes | yes (legacy) | no | no | own session factory |
| `embed_turn_messages` | if `delivery.embed_after_persist` | same | no | no | one batch request for the turn's persisted rows |
| `update_identity` | yes | yes (legacy) | no | no | unchanged body |

D7 recommends flipping the FAILED column to "no" before Phase 2. Because it is one table, that is a one-line change.

### 7.4 Persistence rules (preserving existing guarantees)

1. User and assistant rows are written **together in one transaction** with `created_at` ordered `user < assistant < marker` (the existing `+1 ms` rule, `chat.py:201-202`, `streaming.py:758-759`, kept in the single implementation).
2. `Session.updated_at` is updated in the same transaction (as `streaming.py:798-802`).
3. Interrupted: assistant row written only if the delivered text is non-empty; the system marker row follows (`streaming.py:785-797`, text unchanged).
4. **Idempotency (no schema change):** inside the same transaction, before inserting, `SELECT … FROM messages WHERE session_id=:s AND turn_id=:t AND role=:r LIMIT 1` (`messages.turn_id` is indexed: `models.py:572`; not unique). Skip rows that exist. This replaces the shield-and-flag dance as the *durable* duplicate guard for same-process retries; the existing `asyncio.shield` + `persisted` flag handling in the avatar path (`streaming.py:372-387`) is **kept** because it handles the "commit succeeded but waiter cancelled" case (`test_cancel_during_commit_does_not_duplicate_exchange`).
5. `persist_turn` returns `PersistedTurn(turn_id, user_message_id, assistant_message_id | None, marker_message_id | None)`.
6. Strictness: standard and avatar-bound → raise on failure; typed non-avatar → log and continue (as today, `streaming.py:754,819`).
7. Persisted user text = `ctx.user_message` (stripped), C14 (low-risk normalization).

### 7.5 Transactions and session ownership

| Session | Owner | Lifetime | Content |
|---|---|---|---|
| Request `db` (`get_db`) / `stream_db` | adapter | **preparation only** | session/companion/room/history reads, embedding batch (+ commit when it wrote). Closed/rolled back **before** the LLM call. No DB transaction is held open across generation or the 180 s playback wait (today `stream_db` is held for the whole turn). |
| Persistence session | `persist_turn` | one transaction | messages + `updated_at` |
| Effects session | `run_turn_effects` | one transaction; **SAVEPOINT per effect step** (`begin_nested`) so one failing effect rolls back only itself; a single commit at the end | `ctx.metadata["db"]` / `["db_session"]` swapped for the group (the existing `_run_post_stream_db_write` pattern, `streaming.py:825-864`, generalized and moved) |
| Factory | `TurnDelivery.session_factory` | | `async_session_factory` for standard; `streaming_session_factory` (NullPool) for SSE/spoken, as today |

`timeline_update` and rituals keep their own internal sessions (`timeline.py:144,337`); they are independent of the effects session and unchanged.

### 7.6 `TurnDelivery` (the whole of the legitimate per-mode variation)

```text
TurnDelivery (frozen dataclass; three module constants STANDARD, STREAM, AVATAR)
  session_factory, strict_persistence: bool, embed_after_persist: bool, schedule_title: bool
```

STANDARD: pooled factory, strict, `embed_after_persist=False`, titles on. STREAM: NullPool factory, non-strict, `embed_after_persist=True`, titles on. AVATAR (avatar-bound typed or spoken): NullPool, strict, `embed_after_persist=True`, titles off (today's behavior; D13).

### 7.7 Integration boundary for later phases (reserved; not built)

`CompletedTurn` is the single record a later durable worker consumes. It is a plain dataclass built in `run_turn_effects`:

```text
CompletedTurn: turn_id, session_id, user_id, companion_id, room_id, scene_id, outcome,
               user_text, assistant_text, user_message_id, assistant_message_id, completed_at
```

Phase 2 replaces the body of the `extract_memory` effect (and the post-persist embedding effect) with "insert a durable job keyed by `(session_id, turn_id)`" at exactly this point, with **no** change to adapters. Named integration points, none activated in Phase 0:

| Future capability | Hook (already single after Phase 0) |
|---|---|
| Evolved identity + relationship (Phase 1.3) | `_persona_identity_inputs` `turn.py:2638` returns `(character_profile, identity_state)`; add `relationship_config` there and pass it in `_assemble_persona_prompt` (`turn.py:3075`). Wiring it now would change prompts. |
| Linguistic fingerprint (Phase 6) | `_companion_to_profile_dict` (single copy) and the `assemble(...)` kwargs |
| Durable memory worker (Phase 2) | `extract_memory` effect slot + `CompletedTurn` |
| Reflection / random-drift replacement (Phase 3) | `update_identity` effect slot |
| Persistent mood (Phase 5) | `persist_emotional_state` effect slot |

### 7.8 Failure handling

| Failure | Behavior |
|---|---|
| Generation failure / provider error | Outcome `FAILED`; persisted per §7.2; effects per policy. Provider selection is never substituted (existing, `streaming.py:497-508`, `turn.py:637-642`). |
| Step exception in PREPARE | Outcome `FAILED`; no provider call; **adapter-specific legacy** persistence per D7 (standard persists the error text as today; streaming persists nothing as today) until decided. |
| Persistence failure | Strict: exception to the adapter (HTTP 500 / avatar `persistence_error` → blocks next avatar turn, existing). Non-strict: log; effects are **skipped** (never run effects for an unpersisted turn). |
| Effect failure | Logged, recorded in `turn_effects`, SAVEPOINT rollback; delivered response and persisted messages unaffected. |
| Effects-session commit failure | Logged; all effects of that turn recorded `failed`; nothing retried (Phase 2 owns durability). |
| Trace failure | Logged (existing `_log_trace_warning`); never fails the turn. |

### 7.9 Exactly-once

`ctx.metadata["_persisted"]` and `["_effects_done"]` guard `persist_turn` / `run_turn_effects` per turn object, covering the normal completion racing the `GeneratorExit`/`CancelledError` handler. Cross-process retries are out of Phase 0 scope (clients send no idempotency key; a retry is a new `turn_id`); Phase 2's job table keys on `(session_id, turn_id)`.

---

## 8. Streaming and Cancellation Semantics

### 8.1 Per-mode timeline (target)

| Step | STANDARD | STREAMING typed | SPOKEN |
|---|---|---|---|
| 1 | auth, session 404 | auth, session 404 | WS auth, `_authorize`; transcript event |
| 2 | — | `coordinator.begin` iff avatar bound | `coordinator.begin` (interrupts and waits for prior persistence) |
| 3 | `load_conversation_state` | same, once, after step 2 | same |
| 4 | `build_turn_context`, `prepare_memory_workflow` | same | same (latency marks) |
| 5 | iterate `PREPARE_STEPS` | same; SSE step/`context` events | same |
| 6 | close prep session | same | same |
| 7 | `prepare_provider_call` → `complete()` | `prepare_provider_call` → `stream()`; tokens → SSE (+ `publish`) | same; then `wait_playback` |
| 8 | `respond` | `respond` | `respond` after full playback |
| 9 | `persist_turn` | `persist_turn`, then SSE `response` | `persist_turn` (strict, shielded) then SSE `response` |
| 10 | `run_turn_effects` | `run_turn_effects` | `run_turn_effects` |
| 11 | return JSON | SSE `[DONE]` | `[DONE]` (dropped by `_spoken_turn`) |

**No buffering:** step 7 yields each provider chunk immediately. Nothing is added before the first token.

**SSE contract:** event shapes are unchanged (`typing`, `{step,status,duration_ms}`, `context`, `token`, `response`, `error`, `[DONE]`; `react_ui/src/api/client.ts:840-880` consumes `step`, `token`, `typing`, `response`, `error`, `[DONE]` and ignores unknown types). One observable ordering change (D5): effect-step events (`update_mood`, `persist_emotional_state`, `extract_memory`, `scene_memory_gate`, `timeline_update`, `update_identity`) are emitted **after** `response` rather than before it. `onStep` is optional in the client and no consumer depends on order.

### 8.2 Cancellation and disconnect matrix

| Event | Detection | Outcome | Persistence | Effects | Notes |
|---|---|---|---|---|---|
| Spoken barge-in / `interrupt` | `avatar_turn.interrupted` (`coordinator.py:297-311`) → `CancelledError` (`streaming.py:262-263`) | `INTERRUPTED` | heard prefix + marker, strict, shielded (kept, `streaming.py:436-457`) | none | unchanged; `heard_text` rule unchanged (`coordinator.py:61-69`) |
| Spoken disconnect / takeover | `coordinator._detach` → `interrupt` | `INTERRUPTED` | same | none | takeover waits for `finished` (≤10 s), unchanged |
| Speech failure / playback timeout | `wait_playback` raises (`coordinator.py:271-281`) | `INTERRUPTED` | heard prefix | none | unchanged |
| Skip speech | `turn.speech_skipped` | `COMPLETED` | full text | all | `wait_playback` returns early (existing) |
| Typed+avatar, browser closes SSE after `response` | `CancelledError` with `persisted` | `COMPLETED` | already saved | **shielded continuation runs the effects once** | extends today's identity-only continuation (`:420-435`) to all effects |
| Typed (no avatar) disconnect before persist | `CancelledError` | `ABANDONED` | none (legacy, D8) | none | |
| Typed (no avatar) disconnect after persist | `CancelledError` | `COMPLETED` | saved | shielded effects | **new**: today skipped (C9) |
| Cancellation during the commit | `asyncio.shield(persistence_task)` | unchanged | exactly once (kept) | per outcome | `test_cancel_during_commit_does_not_duplicate_exchange` |
| Provider exception | caught in `prepare_provider_call` consumers | `FAILED` | error text (legacy) | per policy | |

**Shielding rule (one place):** once `persist_turn` has been entered, completion proceeds under `anyio.CancelScope(shield=True)` for persistence and effects. Before that point, cancellation maps to `INTERRUPTED` (avatar) or `ABANDONED` (no avatar).

**`avatar_turn.finished` / `persisted` / `persistence_error` semantics are unchanged**: `finished` is set in the adapter's `finally` after the shielded completion; `interrupt()` still waits ≤10 s for it. Effects must therefore stay bounded: no new network calls beyond the single embedding batch (§11).

**Next-turn visibility:** a new spoken turn begins only after the previous `finished`, so identity/memory effects of turn N are committed before turn N+1 prepares (same ordering as today for identity).

---

## 9. Existing Components to Reuse

| Component | Role in the design | Change |
|---|---|---|
| `TurnContext` (`core/steps.py:11`) | the one turn context | none |
| `TurnPipeline` (`core/turn.py:1137`) | the engine; add `iter_steps`, drop fast-path routing | additive; fast path removed in 0.3 (D2/D14) |
| All 14 step classes | unchanged bodies | `ExtractMemoryStep`: remove the four `_generate_message_embeddings` calls (D12) |
| `_run_steps` | becomes a drain of `iter_steps` | refactor, same semantics |
| `_provider_history`, `_formatted_provider_inputs`, `_provider_config`, `_select_provider` | composed in `prepare_provider_call` | none |
| `_start_turn_trace`, `_record_turn_trace_step`, `_complete_turn_trace`, `_store_llm_trace_metadata` | trace/inspection | none |
| `WorkingMemoryLoader`, `RetrievalEngine`, `SessionAwareVectorStore`, `PgvectorStore` | memory | none (D10: `<->` untouched) |
| `_generate_recent_message_embeddings` (`chat.py:350`) | the one embedding batch helper; also used post-persist for the turn's rows | moved; accepts an optional message-id filter; commit-on-write |
| `_run_post_stream_db_write` (`streaming.py:825`) | seed of the effects runner | generalized and moved |
| `_persist_messages` (`streaming.py:746`) | seed of `persist_turn` | generalized (+idempotency, returns ids), moved |
| `_load_db_session_history` (`chat.py:491`) | seed of the one history loader (superset room predicate) | moved |
| `_session_snapshot` (`streaming.py:867`) | seed of `session_record` | moved |
| `_load_chat_room`, `_load_user_room`, `_validate_room_session`, `_normalize_room_id` | one room resolution | moved |
| `_maybe_schedule_title_generation`, `_generate_conversation_title` | one copy | moved; one of two copies deleted |
| `apply_routing_overrides` (`providers/router.py`) | routing | none |
| `CompanionManager.get` (`companion.py:360`) | companion lookup | none |
| `AvatarCoordinator` / `AvatarTurn` / `_spoken_turn` | avatar | **none** |
| `BackgroundQueue` / `BackgroundWorker` (`app.py:55-64`) | inert after D2; `health.py:29` reads `_background_queue` | left registered; Phase 2 replaces |
| `PersonaCache` | bypassed whenever a profile/identity exists | not passed to the unified pipeline (R8); class and `config.py:446` invalidation left intact |

**Removed after migration** (all duplicates or dead after consolidation): the second copies of `_load_db_session_history`, `_companion_to_profile_dict`, `_first_state_value`, `_maybe_schedule_title_generation`, `_generate_conversation_title`; `_build_metadata`, `_stream_metadata`, `_seed_stream_history_metadata`; `_load_stream_companion_profile`, `_load_stream_session_identity`, `_build_companion_character_profile`, `_load_session_identity_state`, `_load_persona`, `_default_persona` (replaced); `_run_pipeline`; the streaming step loop; `FAST_PATH_STEPS`, `ROOM_FAST_PATH_STEPS`, `DEFERRED_STEPS`, `_FAST_PATH_INTENTS`, `_route_by_intent`, `_run_fast_path`, `run_fast`, `enqueue_deferred`, `_deferred_turn_payload`, `_background_task_type`, and the `core/__init__.py` exports of the first three. `_FallbackPipeline` is import-guard legacy and is left alone.

---

## 10. Files Requiring Modification

| File | Task | Change | Current lines |
|---|---|---|---|
| `chatbot/api/conversation.py` (**new**) | 0.2 (+0.3) | contents per §5.3 | — |
| `chatbot/core/turn.py` | 0.2 | `ProviderInputs`/`prepare_provider_call`, step groups, `iter_steps`, `CallProviderStep.run` | `630-664`, `1252-1316` |
| `chatbot/core/turn.py` | 0.3 | `TurnOutcome`, `EFFECT_POLICY`, `CompletedTurn`; delete fast path/deferral; drop 4 embedding calls | `173-192`, `741/754/764/798`, `1158-1249`, `2055-2080` |
| `chatbot/core/__init__.py` | 0.3 | drop fast-path exports | `3-11` |
| `chatbot/api/chat.py` | 0.2: adapter prelude; delete duplicated helpers. 0.3: persistence/effects via shared calls | `96-242`, `274-345` & helpers |
| `chatbot/api/streaming.py` | 0.2: prelude via shared; provider via `prepare_provider_call`; replace step loop with `iter_steps`. 0.3: epilogue/handlers via outcomes | `79-239`, `245-344`, `350-493`, `496-566` |
| `chatbot/memory/embedding.py` | none (the per-message `generate_embedding` stays as a library function) | — |
| `tests/test_avatar_backend.py` | 0.2/0.3 (rename-coupled patches) | fixtures `:263-336`, `:339-364` |
| `tests/test_provider_routing_integration.py` | 0.2/0.3 | fixtures `:163-250` region |
| `tests/test_retrieval_wiring.py`, `tests/test_chat_embedding_batch.py`, `tests/test_provider_routing.py`, `tests/integration/test_multi_user_isolation.py` | 0.2/0.3 | rename-coupled imports/patches |
| `tests/test_conversation_lifecycle.py` (**new**) | 0.4 | new targeted tests (§12.3) | — |

**Must not be modified:** `chatbot/avatar/*` (router, coordinator, runtime, media), `chatbot/memory/retrieval.py`, `pgvector_store.py`, `extraction.py`, `working_memory.py`, `chatbot/soul/*`, `chatbot/db/models.py`, `migrations/*`, `react_ui/*`, `chatbot/api/config.py`, `chatbot/api/sessions.py`.

---

## 11. Performance and Latency Considerations

### 11.1 Critical path to first text (preserve; add nothing)

Spoken marks recorded today, all preserved with the same names: `stt_complete`, `begin_complete`, `history_ready`, `context_start`, `embedding_prepare_start/complete`, `memory_prepare_start/complete`, `trace_started`, `context_complete`, `coordinator_first_text`, `handler_enter`, `browser_send_complete`, `browser_received` (`streaming.py:178-253`, `chat.py:333-338`, `coordinator.py:197-215`, `router.py:129-137`).

Sequential awaits before the first token (unchanged in kind and count): `begin()` → history+identity load → embedding batch (0 or 1 request) → `room_context` → `retrieve` (1 query embedding + vector search) → EI steps (CPU) → `compile_context` (CPU) → provider.

### 11.2 Call-count budget (per turn)

| Operation | Before (S / T,P) | After (all modes) | Rule |
|---|---|---|---|
| LLM calls on the critical path | 1 / 1 | **1** | no new LLM calls |
| Pre-turn embedding requests | ≤1 batch / ≤1 batch | ≤1 batch | unchanged |
| Query embedding (retrieval) | 1 / 1 | 1 | unchanged |
| Post-turn embedding requests | 0 / **2 sequential** | 0 (standard) / **1 batch** (SSE, spoken) | strictly ≤ today |
| Full-history DB loads | 1 / **2 for avatar-bound** | **1** | one fewer |
| Identity-state DB loads | 1 / **2 for avatar-bound** | **1** | one fewer |
| DB sessions opened per turn | 1 request + 0 / stream_db + 3 | prep + persist + effects (3) | ≤ today for streaming |
| Title LLM call (background, existing) | dormant | dormant | unchanged |
| Open DB transaction across generation | request session / `stream_db` (whole turn, up to 180 s of playback wait) | **none** | improvement |

### 11.3 Consolidation candidates

| Candidate | Status |
|---|---|
| Duplicate history/identity load for avatar-bound turns | **In 0.2** (§6.2). |
| 2 sequential post-turn embedding requests → 1 batch | **In 0.3** (D12). |
| Pre-turn embedding batch writes persisted (`commit`) | **In 0.2** (D12). Probable secondary benefit **[unconfirmed]**: stragglers that were re-embedded every streaming turn stop reappearing, which could further reduce `embedding_prepare` time. The earlier 7.63 s → 0.87 s measurement is consistent with that effect; verify by counting `message_embeddings_generated` across consecutive spoken turns (§12.4). |
| Fold the retrieval query embedding into the straggler batch (one request instead of two) | **Deferred / optional Task 0.5.** Requires `RetrievalEngine.retrieve` to accept a precomputed vector; touches retrieval. Not required for Phase 0. |
| Move straggler embedding entirely off the critical path (post-persist), since pre-turn vectors are invisible to the same turn in production (separate retrieval session, `app.py:48`) | **Deferred**, measurement-gated; changes ordering of when vectors exist. |
| Bounded history query (LIMIT) | **Deferred.** `full_conversation_history` feeds `calculate_drift`; bounding changes drift. Revisit with Phase 3. |

### 11.4 Measurement protocol (targeted only)

1. Spoken fixture with `FakeRuntime` (as `tests/test_avatar_backend.py`): assert the order of the latency marks, and that the first `token` frame is produced before the provider generator finishes (G).
2. Counting fakes for provider `complete`/`stream`, `OpenRouterEmbeddingProvider.embed`, and DB `execute`/`commit`; assert §11.2.
3. One manual/real-stack run (user-driven): STT-complete → first browser text on the Mercury setup, compared with the **2.04 s** reference, and `embedding_prepare` compared with **0.87 s**. Reference values are historical, not an SLA.

---

## 12. Regression Testing Requirements

### 12.1 Existing coverage and baseline (run in this task, targeted only; no full suite)

Command scope: `tests/test_chat_embedding_batch.py`, `tests/test_retrieval_wiring.py`, `tests/test_chat_e2e.py`, `tests/test_avatar_backend.py`, `tests/test_provider_routing_integration.py`, `tests/test_background_worker.py`, `tests/test_streaming_integration.py`.

| File | Result | Meaning |
|---|---|---|
| `test_avatar_backend.py` (30+ tests) | **pass** | Strong coverage of spoken/typed streaming, interruption persistence, commit-cancel, skip-speech, takeover; but via heavy monkeypatching of `streaming` internals |
| `test_provider_routing_integration.py` | **pass** | Standard vs streaming provider destinations, thinking, Ollama no-fallback, spoken routing |
| `test_retrieval_wiring.py` (4) | **pass** | `RetrieveStep` engine, `_prepare_memory_workflow`, `_stream_turn` wiring |
| `test_chat_embedding_batch.py` (1) | **pass** | one ordered batch request |
| `test_chat_e2e.py` | **13 of 17 fail at baseline** | Stale: `assert 14 == 6` (step count) and 401 "Not authenticated" (no auth override). No working end-to-end coverage of `/chat/` or `/chat/stream` through a real app. |
| `test_background_worker.py` | **4 of 4 fail** | `ModuleNotFoundError` / `AttributeError`; pre-existing |
| `test_streaming_integration.py` | **collection error** | Flask-era; imports nonexistent `chatbot.app` |

Two runs were made. Run 1 (`test_chat_embedding_batch` + `test_retrieval_wiring` + `test_chat_e2e -x`): 5 passed, then stopped at the first `test_chat_e2e` failure. Run 2 (`test_chat_e2e` + `test_avatar_backend` + `test_provider_routing_integration` + `test_background_worker`): **69 passed, 17 failed** (13 in `test_chat_e2e`, 4 in `test_background_worker`). `test_streaming_integration` fails at collection. None of the failures is caused by this task. `tests/integration/*` need a running PostgreSQL (not available here) and were not run.

**Name-coupling warning.** The passing tests patch private names that this refactor moves or deletes: `chat_api._build_companion_character_profile`, `_load_session_identity_state`, `_load_chat_room`, `_load_db_session_history`, `_prepare_memory_workflow`, `_maybe_schedule_title_generation`, `_run_pipeline`; `streaming._load_stream_companion_profile`, `_load_stream_session_identity`, `_load_db_session_history`, `_persist_messages`, `_run_post_stream_db_write`, `_maybe_schedule_title_generation`, `_session_snapshot`, `streaming_session_factory`, `build_default_pipeline`, `_generate_recent_message_embeddings`, and the `_stream_turn(**kwargs)` signature (`tests/test_avatar_backend.py:339-364`, `tests/test_provider_routing_integration.py`, `tests/test_retrieval_wiring.py:143-199`). The implementing task **updates these tests to patch the new shared names**. Shims that keep the old names alive are explicitly rejected (no temporary compatibility layers).

### 12.2 Tests to keep passing unchanged in behavior

`test_one_generation_shared_tokens_memory_and_prompt_trace`, `test_spoken_barge_in_persists_heard_prefix_before_next_typed_turn`, `test_disconnect_cancel_scope_still_persists_interruption`, `test_no_avatar_keeps_existing_text_path`, `test_real_interrupted_persistence_omits_unheard_assistant`, `test_cancel_during_sse_send_explicitly_closes_and_persists`, `test_cancel_during_commit_does_not_duplicate_exchange`, `test_failed_persistence_blocks_subsequent_avatar_turn`, `test_zero_token_provider_does_not_trigger_second_generation`, `test_companion_change_rejects_spoken_generation`, `test_takeover_refuses_to_overtake_unsettled_persistence`, `test_spoken_text_is_delivered_before_speech_finishes`, `test_typed_text_does_not_wait_for_speech`, all `skip_*` tests, `test_typed_speech_survives_the_browser_closing_sse_after_the_full_response`, `test_closing_sse_before_the_response_is_saved_still_interrupts`, and all of `test_provider_routing_integration.py`. Assertions change only where an approved decision (D-list) changes behavior, and then deliberately.

### 12.3 New targeted tests (`tests/test_conversation_lifecycle.py`, Task 0.4 owns the file)

Shared fakes: a capturing `Provider` (records `complete`/`stream` kwargs), a recording embedding provider, a recording fake DB session factory, and the existing `FakeRuntime`.

| ID | Proves | Method |
|---|---|---|
| **T0** | Characterization of C5 against real PostgreSQL | Opt-in marker (`@pytest.mark.postgres`, skipped when `pg_isready` fails). Real session; run the in-loop `ExtractMemoryStep` + `PersistEmotionalStateStep` on `stream_db`, call `rollback()`, assert rows absent. Run **before** 0.3 to confirm F1, **after** to assert rows present. |
| **A** | Equal inputs → equal provider-ready messages across modes | One state fixture; drive standard (`complete` captured) and streaming and spoken (`stream` captured); assert equal `(prompt, history, model, temperature, top_p, thinking)` and equal `ctx.compiled_context`. |
| **B** | Personality identical across modes | Same assertion focused on the system message's persona/identity sections (C1/C2) with a companion profile and a persisted mood. |
| **C** | Memory retrieval consistent | `retrieve.assert_awaited_once_with(query=…, session_id=…[, room_context])` identical in all three; scene-isolated room → same `room_context`. |
| **D** | Persistence rules identical | Same message rows (order, `created_at` monotonic, `turn_id` shared, stripped text, `updated_at`) for completed turns in all modes; idempotent second call inserts nothing. |
| **E** | Completed turns trigger the same effects | Recording effects: identical set/order of effect steps for COMPLETED in all modes; `turn_effects` map identical; scene isolation gate identical. |
| **F** | Interrupted turns don't corrupt state | INTERRUPTED: user + heard prefix + marker only; zero effects; no duplicate rows after cancel-during-commit; ABANDONED: zero rows. |
| **G** | Streaming begins before generation completes | Provider generator blocked on an event after the first chunk; assert the first `token` frame is yielded while the generator is still pending. |
| **H** | No additional LLM/embedding calls | Counting fakes: provider calls = 1; embedding requests per §11.2; DB commits ≤ budget. |
| **I** | Provider/avatar behavior intact | Existing suites in §12.2 unchanged. |
| **J** | Latency preserved | Latency-mark order test (§11.4 #1) and call-count test; no wall-clock threshold in CI. |
| **K** | Failed-turn policy table | `EFFECT_POLICY[FAILED]` matches the approved D7 value. |
| **L** | Isolation | Two concurrent turns for two sessions/users: no cross-session history, identity, or effects; per-turn `TurnPipeline` instances are not shared. |

### 12.4 Explicit acceptance criteria (Phase 0 as a whole)

1. `chat.py` and `streaming.py` contain no private copies of: history load, companion profile dict, identity load, persona resolution, metadata builder, message persistence, title scheduling, provider-input construction, effects dispatch (grep-checkable).
2. T-A, T-B, T-C, T-D, T-E pass for all three modes.
3. All §12.2 tests pass with only rename-driven edits plus decision-driven assertion changes.
4. Provider calls = 1 per turn; embedding requests per turn ≤ today in every mode (§11.2); no new network call before the first token.
5. First `token` frame precedes provider completion (G); latency marks preserved (J).
6. After 0.3, streaming/spoken completed turns persist the same effect rows as standard (T0 inverse), and interrupted/abandoned turns persist none.
7. No schema, dependency, or avatar-module change in the diff.
8. Real-stack check (user-run): TTFT within noise of the 2.04 s reference; `embedding_prepare` ≤ 0.87 s.
9. Decisions D1–D8 and D12 recorded as approved before the tasks that implement them start.

---

## 13. Task 0.2 Implementation Specification — Shared preparation

> **Amended by Addendum A.** Items that remove or alter the fast path, add step groups/`iter_steps`, change saved-state or room-prompt behavior, or commit the embedding batch are **not** part of Task 0.2. See §A.3 for what was implemented.

**Objective.** One authoritative preparation path for all three modes: identical state loading, persona resolution, context building, memory-workflow prep, step execution up to the prompt, and provider inputs. Delivery and completion untouched except where they must call the shared pieces.

**Required decisions first:** D1, D2, D3, D4, D6, D11, D12-part (commit-on-write).

**Required files:** `chatbot/api/conversation.py` (new), `chatbot/core/turn.py`, `chatbot/api/chat.py`, `chatbot/api/streaming.py`, the rename-coupled tests (§12.1).

**Exact architectural changes**

1. Create `api/conversation.py` and move into it: `_load_chat_room`, `_load_user_room`, `_validate_room_session`, `_normalize_room_id`, `_build_room_system_prompt`, `_stringify_prompt_value`, `_prepare_memory_workflow` (→ `prepare_memory_workflow`), `_generate_recent_message_embeddings`, `_metadata_retrieval_engine`, `_build_chat_retrieval_engine`, `_session_snapshot`, one `_first_state_value`, one `_companion_to_profile_dict`.
2. Add `load_conversation_state(db, user, session_row, requested_companion_id, requested_room_id) -> ConversationState` (§6.2): effective companion (session binding, else requested; mismatch → 400 as `chat.py:116-120`), profile via `CompanionManager.get`, identity dict, room (superset semantics), history (single SQL with the `chat.py:501-514` predicate), `session_record` snapshot.
3. Add `resolve_persona(...)` per D1 and `build_turn_context(...)` (§6.3), the **only** place that writes `TurnContext` metadata. Delete `_build_metadata`, `_stream_metadata`, `_seed_stream_history_metadata`, `_load_persona`, `_default_persona`, `_persona_from_state`, `_first_persona`.
4. `prepare_memory_workflow`: unchanged contract and marks; `await db.commit()` iff vectors were written (D12). `test_chat_embedding_batch.py` updated for the new import path.
5. `core/turn.py`: add `ProviderInputs` and `prepare_provider_call(ctx)` composed from `_select_provider`, `_provider_config`, `_provider_history`, `_formatted_provider_inputs`, `_render_trace_prompt`. Rewrite `CallProviderStep.run` to use it (error text and metadata identical to today). Rewrite `_stream_provider_tokens` to use it (keeps the "no stream method → fall back to `CallProviderStep`" branch and the `"Provider stream error:"` token convention that `_stream_turn` already interprets).
6. `core/turn.py`: add `PREPARE_STEPS`, `GENERATE_STEPS`, `COMPLETION_STEPS`, and `TurnPipeline.iter_steps(ctx, names)`; reimplement `_run_steps` as a drain of it (same trace, `step_results`, and exception-to-`pipeline_error` semantics). Add `TurnPipeline.run_completion_steps` only as the interim composition below.
7. `chat.py::chat()` becomes: session lookup (edge) → `load_conversation_state` → `build_turn_context` → `prepare_memory_workflow` → `iter_steps(PREPARE_STEPS)` → `CallProviderStep` → `respond` → **interim**: run `COMPLETION_STEPS` on the request session, persist, `db.commit()` exactly as today, then `ChatResponse`. This interim epilogue is today's code, replaced wholesale in 0.3; it is not a compatibility layer. `execute_turn` is no longer called (D2); `build_default_pipeline` is invoked through `conversation.build_pipeline()` (no persona cache, no background queue).
8. `streaming.py`: `stream_chat` keeps session lookup/404 and the avatar 409 precondition; the loads move into `_stream_turn` (after `begin()` for avatar, once). `_stream_turn` prelude uses `load_conversation_state`/`build_turn_context`; the per-step loop is replaced by `async for event in pipeline.iter_steps(...)` emitting the same SSE step events; provider branch uses `prepare_provider_call`. Epilogue/handlers untouched in this task.
9. Close the preparation session after `PREPARE_STEPS` (no transaction across generation).

**Existing functions to reuse:** all listed in §9.

**Dependencies:** approvals above. No code dependency on 0.3. 0.4's harness can proceed in parallel against the §6 signatures.

**Expected behavior after 0.2**
- Provider inputs are identical across modes (A) and formatted in all (D4).
- System prompt identical across modes (B); mood/user-state/mode carried in standard (D3).
- Room prompt delivery per D6; room/companion semantics unified.
- SSE events, token streaming, spoken handoff, avatar interruption behavior unchanged.
- Standard chat no longer takes the fast path (D2): casual/greeting/feedback turns run the full step list and no `BackgroundQueue` task is enqueued.
- Pre-turn embedding batch writes are committed.
- Avatar-bound turns perform one history and one identity load.

**Regression tests:** §12.2 (updated for renames), new A, B, C, G, H(partial), L, plus `test_chat_embedding_batch` on the new path.

**Acceptance criteria:** §12.4 items 1 (for the preparation concerns), 2 (A, B, C), 3, 4, 5, 7.

---

## 14. Task 0.3 Implementation Specification — Shared persistence and post-turn lifecycle

**Objective.** One completion lifecycle: outcome classification, idempotent persistence, isolated effects with explicit eligibility, shielded continuation, one embedding batch, one title scheduler. Fixes C5 without a new background system.

**Required decisions first:** D5, D7, D8, D12, D13, D14.

**Required files:** `chatbot/api/conversation.py`, `chatbot/core/turn.py`, `chatbot/core/__init__.py`, `chatbot/api/chat.py`, `chatbot/api/streaming.py`, rename-coupled tests.

**Exact architectural changes**

1. `core/turn.py`: add `TurnOutcome`, `EFFECT_POLICY` (§7.3), `CompletedTurn` (§7.7). Remove `FAST_PATH_STEPS`, `ROOM_FAST_PATH_STEPS`, `DEFERRED_STEPS`, `_FAST_PATH_INTENTS`, `_route_by_intent`, `_run_fast_path`, `run_fast`, `enqueue_deferred`, `_deferred_turn_payload`, `_background_task_type`, the `_session_turn_counts`/`_background_queue` constructor state, and the three `core/__init__.py` exports (D14). Remove the four `_generate_message_embeddings` calls inside `ExtractMemoryStep` (`741,754,764,798`).
2. `conversation.persist_turn(ctx, *, outcome, delivery, user, session_id, companion_id, delivered_text=None) -> PersistedTurn`: generalizes `_persist_messages` (§7.4: one transaction, ordered timestamps, `updated_at`, interrupted marker, idempotency pre-check, returns ids, strictness from `delivery`). Preserves the avatar shield: callers pass the coroutine through the existing `asyncio.create_task` + `asyncio.shield` pattern (`streaming.py:372-387`).
3. `conversation.run_turn_effects(ctx, pipeline, *, outcome, delivery, persisted)`: opens **one** session from `delivery.session_factory`, swaps `ctx.metadata["db"]`/`["db_session"]`, runs `COMPLETION_STEPS` filtered by `EFFECT_POLICY[outcome]` via `iter_steps`, each step in a `begin_nested()` SAVEPOINT, then (if `delivery.embed_after_persist`) the embedding effect using `prepare`'s batch helper filtered to `persisted` ids (one request), then commits once, restores metadata, records `ctx.metadata["turn_effects"]`, emits one `turn_completed` log line, and completes the trace (`_complete_turn_trace`). Builds and exposes `CompletedTurn`. Guarded by `_effects_done`.
4. `chat.py::chat()`: after generation, `persist_turn(... STANDARD ...)` → `run_turn_effects(...)` → `ChatResponse`. Remove inline `Message` inserts, `db.commit()`, and `_maybe_schedule_title_generation` duplicate (calls the shared scheduler per `delivery.schedule_title`).
5. `streaming.py::_stream_turn`: replace `:346-415` and handlers `:417-493` with outcome-based calls in the order *persist → emit `response` → effects → `[DONE]`*; map events to outcomes per §8.2; shielded continuation per §8.2; keep `avatar_turn.persisted`/`persistence_error`/`finished`/`stop_speech` semantics exactly. Remove `stream_db.rollback()` (the preparation session is already closed). Step-exception handling follows D7 (legacy per-adapter default).
6. Delete `_persist_messages`, `_run_post_stream_db_write`, `_generate_message_embeddings` call at `streaming.py:401`, and the duplicated title functions.
7. Update rename-coupled tests: patch `conversation.persist_turn` / `run_turn_effects` / session factory where they patched `_persist_messages` / `_run_post_stream_db_write`.

**Existing functions to reuse:** `_persist_messages` body, `_run_post_stream_db_write` body, the three trace helpers, `UpdateIdentityStep`, `ExtractMemoryStep`, `TimelineUpdateStep`, `PersistEmotionalStateStep`, `UpdateMoodStep`, `SceneMemoryGateStep`, `_generate_recent_message_embeddings`.

**Dependencies:** 0.2 merged and reviewed.

**Expected behavior after 0.3**
- COMPLETED turns in standard, streaming and spoken persist identical message rows and trigger the identical, explicit effect set, now committed (C5 fixed; **new persisted data in streaming/spoken:** `semantic_facts`, `episodes`, `bot_mood_logs`, `user_state_logs`, `sessions.user_state`/`conversation_mode`, which is D5/D12 made visible).
- INTERRUPTED persists the heard prefix and marker only; ABANDONED persists nothing; neither runs effects.
- Disconnect after persistence cannot skip effects in any mode.
- Streaming/spoken post-turn embedding: 2 sequential requests → 1; standard unchanged.
- Each turn records per-effect `applied | skipped | failed` in the trace and a log line.
- No DB transaction spans generation or playback.

**Regression tests:** §12.2, new D, E, F, H, K, L, T0 (before and after), plus `test_cancel_during_commit_does_not_duplicate_exchange` and `test_failed_persistence_blocks_subsequent_avatar_turn` unchanged in assertions.

**Acceptance criteria:** §12.4 items 1 (completion concerns), 2 (D, E), 3, 4, 6, 7, 8.

### 14.1 Additional task justified (not in the original numbering)

**0.4 — Test harness and characterization (tests only).** Justified because the existing suite has no working end-to-end coverage of `/chat/` or `/chat/stream` (13 stale failures), the safety-critical fixtures are heavily monkeypatched against names this refactor moves, and F1 needs a real-database characterization (T0). It owns `tests/test_conversation_lifecycle.py` and shared fakes; it touches no application source.

**0.5 — Optional, measurement-gated:** fold the query embedding into the straggler batch (§11.3). Not required for Phase 0 acceptance.

---

## 15. Parallel Execution Opportunities

### 15.1 Dependency graph

```
 decisions D1–D8, D11, D12 approved
        │
        ├──► 0.4 test harness (tests only; contracts from §6–§7) ─────────────────┐
        ▼                                                                          │
      0.2 shared preparation  ──(reviewed)──►  0.3 shared completion ──► Phase 0 acceptance
                                                    ▲                              │
        0.4 flips xfail → pass after 0.2 (A,B,C,G) and 0.3 (D,E,F,H,K,L) ◄────────┘
```

### 15.2 Can 0.2 and 0.3 run concurrently?

**No.** Both rewrite the same regions of `chat.py::chat()`, `streaming.py::_stream_turn()`, and `core/turn.py`, and both create/extend `api/conversation.py`. The 0.3 epilogue also depends on 0.2's step groups and `iter_steps`. Run them sequentially with a review gate between them.

### 15.3 What can run in parallel

| Work | Parallel with | Condition |
|---|---|---|
| **0.4** test harness (new tests + fakes) | 0.2 | tests-only; signatures frozen by §6–§7; new tests marked `xfail(strict=False)` until their task lands |
| T0 against real PostgreSQL | anything | opt-in; read-only on source |
| Within 0.2: `core/turn.py` provider-input + iterator work vs `api/conversation.py` loaders | each other | possible (different files) but they share the `prepare_provider_call` / `iter_steps` names; recommend **one agent** to avoid interface drift |
| Documentation/decision-log updates | anything | `docs/` only; never modify the five planning docs |

### 15.4 Single implementation owner per file (no concurrent editors)

| File | Owner while active |
|---|---|
| `chatbot/core/turn.py` | 0.2, then 0.3 |
| `chatbot/api/conversation.py` | 0.2, then 0.3 |
| `chatbot/api/chat.py` | 0.2, then 0.3 |
| `chatbot/api/streaming.py` | 0.2, then 0.3 |
| `tests/test_avatar_backend.py`, `tests/test_provider_routing_integration.py`, `tests/test_retrieval_wiring.py`, `tests/test_chat_embedding_batch.py`, `tests/test_provider_routing.py`, `tests/integration/test_multi_user_isolation.py` | whichever of 0.2/0.3 changes the patched name (never both at once) |
| `tests/test_conversation_lifecycle.py` | 0.4 only |
| `chatbot/avatar/*`, `chatbot/db/*`, `migrations/*` | nobody (frozen) |

---

## 16. Risks and Unresolved Decisions

### 16.1 Decisions requiring approval

> **Amended by Addendum A:** D2 and D14 reversed; D1 modified (A2); D3 and D6 withdrawn (now U1, U2); D5, D7, D8, D12, D13 deferred to Task 0.3.

Each row: recommendation, the default if no decision, and what changes observably. "Default" always preserves a legacy behavior unless stated.

| ID | Needed before | Decision | Recommendation | Default if undecided | Observable change |
|---|---|---|---|---|---|
| **D1** | 0.2 | Persona input for the system prompt (C1): drop the synthesized `DEFAULT_PERSONALITY` and use `{}` unless an explicit persona exists, vs keep it | **Streaming's behavior** (`{}`): no synthesized generic persona on top of the companion profile; aligns with A15 and the roadmap's anti-generic-assistant goal | Cannot be "legacy" (modes disagree). Block 0.2 until chosen. | Standard chat's system prompt text changes (loses the "helpful chatbot companion" block) |
| **D2** | 0.2 | Retire intent-based fast path; all modes run the full step list | **Retire.** Streaming/spoken already do; EI steps are in-process CPU; avoids moving more work into the in-memory queue the roadmap replaces; supports P03 | Block | Standard casual turns gain mood/mode in the prompt and run extraction inline; no more `BackgroundQueue` `memory_extract` tasks (consolidation was never enqueued anyway) |
| **D3** | 0.2 | Standard chat reads persisted mood/user-state/mode like streaming (`session_record` in all modes) | **Yes** | Block | Standard chat's mood carries across turns instead of resetting |
| **D4** | 0.2 | Streaming adopts `_formatted_provider_inputs` | **Yes** (required for equivalence) | Block | Streamed provider payload normalized (system lifted/merged, whitespace-stripped); inspector gains `llm_prompt_payload/family` |
| **D5** | 0.3 | Completion order *persist → `response` → effects* with isolated sessions; effect-step SSE events after `response`; **legacy extraction/mood effects become real for streaming/spoken** (the architecture doc's §3.4 "enable the legacy extractor" question) | **Approve.** After completion, off the pre-token path, no model calls, no backfill, one extraction per turn | Block | New rows in `semantic_facts`, `episodes`, `bot_mood_logs`, `user_state_logs` for streaming/spoken sessions; SSE effect events reorder |
| **D6** | 0.2 | Room-type prompt (`render_room_prompt`): deliver every turn in all modes through the existing `room_scene` layer, vs today's accidental first-turn-standard-only | **Deliver every turn, all modes** | Preserve nothing consistent is possible; if undecided, **omit in all modes** (equal to today's streaming) | Room sessions' prompts gain the room-type text on later turns (standard) and all turns (streaming) |
| **D7** | 0.3 | Failed turns: (a) gate effects off for `FAILED`; (b) stop persisting error text as an assistant message; (c) unify step-exception handling | **(a) yes, (c) persist user message and no assistant row on step exception, (b) yes**, once approved | Legacy per adapter (standard persists error text and runs effects; streaming persists nothing on step exception) | Memory/timeline/identity no longer fed by `"Provider error: …"` text; standard no longer stores error strings as assistant messages |
| **D8** | 0.3 | Typed SSE disconnect before persistence (no avatar): keep "persist nothing" or persist user message + delivered prefix as `INTERRUPTED` | **Keep legacy** for Phase 0; revisit with a product decision | Legacy | none |
| **D9** | 1.3 | Conversation history appears twice in the provider payload (flattened in system + role messages) | Defer | unchanged | none |
| **D10** | 2.4 | `<->` (L2) vs `<=>` (cosine) in `PgvectorStore.search` | Do not touch in Phase 0 | unchanged | none |
| **D11** | 0.2 | Shared module location: new `api/conversation.py` vs `chat.py` | **New module** (§5.3) | `chat.py` + imports from `streaming.py` | none |
| **D12** | 0.2/0.3 | Embedding policy: pre-turn batch writes **committed**; post-turn embedding is **one batch** for SSE/spoken, none for standard | **Approve** | Block | Vectors previously discarded in streaming now persist (adds data only); fewer post-turn requests |
| **D13** | 0.3 | Title generation: dedupe only, same per-mode policy (dormant) | Dedupe only | same | none |
| **D14** | 0.3 | Delete fast-path/deferral code rather than keep it unused | **Delete** (no external callers outside `core/__init__.py`) | keep unused | none |

### 16.2 Risks

| ID | Risk | Mitigation |
|---|---|---|
| **R1** | F1 is **[Static]**. PostgreSQL was unavailable, so the claim that streaming discards in-loop writes rests on code reading plus SQLAlchemy savepoint/flush semantics. | T0 against real PostgreSQL before 0.3; the design is correct either way (isolated commit), but the *observable* delta in §14 depends on it. |
| **R2** | Moving effects after persistence reorders SSE effect events (D5). | Client consumes step events optionally (`client.ts:840-849`); confirm the avatar browser UI does not key on event order (not inspected; the avatar frontend is outside this task's reading). |
| **R3** | Prompt-visible changes (D1, D3, D4, D6) alter companion behavior in standard chat. | Each is explicit and independently revertible; test A/B pin the final text; user reviews a before/after prompt diff for one fixture. |
| **R4** | Rename-coupled tests (§12.1). A careless refactor could weaken the interruption/commit-race tests when they are re-pointed. | Assertions in §12.2 must not be loosened; reviewer diffs test assertions separately from fixtures. |
| **R5** | `finished` is set after effects, and `interrupt()` waits ≤10 s for it (`coordinator.py:308-311`). Slow effects delay barge-in. | Effects stay bounded (no network beyond the single embedding batch); single batch replaces two sequential calls. |
| **R6** | Closing the preparation session before generation changes which objects remain attached (`session_record` is a dict snapshot, not an ORM row). | `_load_db_session_record` (`turn.py:3935`) already falls back to `db_session.get`; effects use their own session. Covered by tests E/D. |
| **R7** | `ctx.metadata["mood_state"]` is overloaded (mood engine vs `UpdateIdentityStep`'s evolution mood, `turn.py:889`), and `sessions.mood_state` is overwritten by the latter. | Out of Phase 0 scope; record for Phase 1/5 design. |
| **R8** | Persona cache (`chatbot/perf/cache.py`) embeds mood/mode in cached prompts for companion-less sessions; the unified pipeline does not pass it. | Effect is limited to turn 1 of companion-less sessions (identity rows exist from turn 2). Class and `config.py:446` invalidation stay valid and inert. |
| **R9** | No per-session lock: two concurrent typed turns on one session can interleave (identity last-writer-wins, as today). | Documented; no new lock (A10). Phase 1 designs optimistic versioning. Avatar turns are already serialized by `binding.lock`. |
| **R10** | `messages.turn_id` is not unique; the idempotency pre-check is not race-proof across processes. | Phase 0 is single-process per turn owner; Phase 2's job table adds a real key. No schema change here. |
| **R11** | The `_stream_turn(**kwargs)` signature is test-visible; moving loads into the generator changes when load errors surface (HTTP 500 before streaming vs an SSE error event). | Keep session-lookup 404 and avatar 409 at the endpoint; accept that load failures become SSE `error` events. |

### 16.3 Open questions for the product owner

1. **D1** is the single most consequential choice: which system prompt is the intended one? The recommendation is the streaming/spoken one.
2. Is it acceptable that enabling extraction/mood persistence for streaming/spoken (D5) starts writing data that has been silently dropped until now? The architecture doc anticipated this question (§3.4); this design says yes, with no backfill.
3. Should typed SSE disconnects ever persist partial turns (D8)?

### 16.4 Out of scope here, recorded for later phases

Persistent identity/relationship (1.x); durable jobs and semantic extraction (2.x); `<->` vs `<=>`, selective forgetting (2.4); random drift replacement (3.x); relationship/fingerprint wiring (1.3, 6); history duplication and token budgeting (1.3+); retrieval query-embedding fold-in (0.5, optional).
