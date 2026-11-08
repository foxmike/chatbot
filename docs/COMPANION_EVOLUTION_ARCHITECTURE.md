# Companion Evolution — Architecture and Phase 0 Consolidation Specification

**Project root:** `~/dev/mike/chatbot/python/chatbot`  
**Status:** Proposed architecture for interactive review; **no source changes authorized** by this file  
**Primary requirements:** `docs/COMPANION_EVOLUTION_REQUIREMENTS.md`  
**Companion implementation evidence:** `docs/COMPANION_MEMORY_CONTEXT_AUDIT.md`, `docs/COMPANION_EVOLUTION_PLANNING_RESEARCH.md`  
**Prepared:** 2026-11-07

## 1. Architecture intent

The chatbot should expose **one set of companion semantics** regardless of whether replies arrive as a complete HTTP response, SSE tokens, or spoken-avatar WebSocket events. The delivery modes are different, but they must not carry independent implementations of identity loading, memory retrieval, prompt construction, message persistence, and completed-turn processing.

**Phase 0 is a narrowly scoped prerequisite.** It must remove actual duplicated business logic and standardize completed-turn behavior, while preserving existing transports and streaming latency. It must **not** implement persistent identity, semantic extraction, durable jobs, or new personality behavior ahead of their later phases.

### Desired logical flow (not a mandate to create classes)

```text
HTTP /chat/          HTTP /chat/stream          Avatar /api/avatar/ws
       \                   |                         /
        \            delivery-specific handling     /
         +------------------+-----------------------+
                            |
               Shared companion turn preparation
           (ownership, identity, context, retrieval,
             prompt layers, budget, provider inputs)
                            |
                    Existing LLM providers
                  complete() / stream() as needed
                            |
              Shared turn finalization / persist
       (exactly-once eligible effects, outcome semantics)
                            |
             Existing background dispatch boundary
            (durable semantic jobs added in Phase 2)
```

**Do not create three new wrappers around three old pipelines.** The outcome must have one authoritative place for each shared responsibility. Prefer extracting/reusing existing functions and pipeline steps over inventing another orchestration layer.

## 2. Code map reported by AGY — verify before changing

| Responsibility | Reported existing component | Phase 0 treatment |
|---|---|---|
| Standard chat | `chatbot/api/chat.py`, `TurnPipeline.execute_turn()` in `chatbot/core/turn.py` | Keep HTTP semantics; delegate shared preparation/finalization. |
| SSE streaming | `chatbot/api/streaming.py`, imperative stream generator | Keep token/SSE delivery; remove duplicated companion semantics. |
| Spoken text | `chatbot/avatar/router.py` invoking `stream_chat` | Continue using streaming route; do not touch AVTR/media or voice runtime. |
| Companion identity | `CompanionManager`, `PersonaPromptAssembler` | One effective identity resolution and prompt layer implementation. |
| Working history | `WorkingMemoryLoader` / `messages` | One selection and ordering policy. |
| Embedding + retrieval | `_generate_recent_message_embeddings`, `_prepare_memory_workflow`, `RetrievalEngine` | Preserve single-batch optimization and retrieval ordering. |
| Context creation | `CompileContextStep`, `_compile_context_text`, `compose_prompt_layers` | One canonical provider-facing input; no competing assemblers. |
| Post-turn updates | `ExtractMemoryStep`, `TimelineUpdateStep`, `UpdateIdentityStep`, stream post-turn logic | One completion contract; retain legacy semantics until approved replacements. |
| Persistence | regular DB commit; stream `_persist_messages` | Share rules for transaction boundary, completed outcome, cancellation and deduplication. |
| Trace/prompt inspection | `TraceRecord`, context display / admin traces | Keep truthful view of the exact effective provider inputs. |

These are audit findings, not a substitute for inspecting live repository code. The report also calls pgvector `<->` "cosine" distance, but `<->` is **L2/Euclidean** and `<=>` is **cosine** in pgvector. Phase 0 should verify actual operators and metrics **without silently changing retrieval behavior**. A separate, explicitly accepted fix may be needed if a discrepancy is confirmed.

## 3. Phase 0 contracts

### 3.1 Shared turn preparation

**Input:** authenticated user, companion, session/room, incoming message, existing model/provider parameters and context/trace settings. **Output:** canonical provider request inputs + diagnostics, preserving all existing content and ordered roles.

The shared implementation owns:

- Companion/relationship lookup using current ownership rules.
- Relevant persisted identity/mood (as implemented now; cross-session changes belong to Phase 1).
- Conversation history selection, room and scene isolation, memory retrieval and ordering.
- Recent-message embedding batching with existing configured embedding provider; **do not reintroduce sequential remote calls**.
- Persona, relationship, mood, room/scene overlays and token-budget behavior.
- The exact role-tagged payload used by providers and a corresponding accurate inspection/trace representation.

**Non-goal:** no new provider APIs, no model calls for behavioral planning or extraction, no new database tables, and no rewriting token-by-token streaming.

### 3.2 Shared turn finalization

**Input:** logical `turn_id`, content, outcome (`completed`, `interrupted`, `failed` or equivalent existing semantics), durable persistence state, and current user/session/companion references.

**Contract:** persist a completed turn once; apply/enqueue only the post-turn effects defined for that outcome once; never make non-streaming and streaming modes silently disagree about which completed-turn effects are eligible. Use existing stable IDs and transactions wherever possible. Specify how assistant partial messages are recorded, if at all, without accidentally reflecting on them as completed experiences.

A completion should not be confused with "WebSocket connection still open" or "TTS playback finished": the companion turn can be complete after provider generation/persistence while audio plays independently. Interrupted and disconnected turns require explicit observed behavior, not inferred success.

### 3.3 Lifecycle stages: shared responsibility, distinct mechanics

| Stage | Must be shared? | Allowed path-specific behavior |
|---|---|---|
| Resolve user/companion/session/room | **Yes** | Different auth/transport entry point. |
| Identity/mood/relationship reads | **Yes** | Existing feature flags/room scope where intentional. |
| Retrieve memory + working history | **Yes** | No change to selection based merely on delivery type. |
| Prompt assembly and inspection | **Yes** | Formatting to provider native payload, when required. |
| LLM generation | Shared provider interface | `complete()` vs `stream()`; no forced buffering. |
| Persist complete result | **Yes** | Cancellation/partial-turn edge cases recorded consistently. |
| Post-turn state and memory hooks | **Yes** | Legacy step eligibility must be explicit; no new synchronous calls. |
| Emission to user | **No** | HTTP JSON, SSE, avatar text/WebSocket/TTS. |
| Browser and media behavior | **No** | Keep current avatar/voice pipeline untouched. |

### 3.4 Critical transition decision: legacy regex extraction

The research reports **regex facts/episodes/timeline in standard chat but not SSE/spoken**. A careless unification would either (a) keep these modes semantically different or (b) unexpectedly enable unapproved side effects on streaming sessions.

Phase 0 must first trace the real code and define a single turn-completion event/hook across all paths. For the existing legacy memory handler, document and test one approved, uniform policy. If enabling the legacy extractor for SSE/spoken is necessary to meet same-effects acceptance, it must run **after completion**, off the pre-token path, and not introduce paid model calls. It must not double-extract or backfill existing sessions. Replacing regex with meaningful semantic extraction and durable dispatch belongs to Phase 2; do not build an elaborate temporary engine in Phase 0.

The operator should be able to see from trace/log evidence whether each completed turn was processed, skipped by an explicit policy, or failed. No silent omission based solely on transport.

## 4. Cancellation and retry invariants

- Turn cancellation cannot mutate a newer turn's state or produce memory/identity effects based on unfinished generation.
- "Generated text", "saved message", "post-turn work scheduled", and "work applied" are distinct milestones.
- Retries or reconnections cannot duplicate messages or completed-turn side effects.
- AVTR/TTS finishing late must not reactivate a cancelled conversation turn.
- Preserve the recent avatar sink ownership and renderer pause/resume fixes; **do not refactor avatar media** in this workstream.
- Concurrent sessions may read/update relevant state without accidental cross-session overwrites; full persistent identity concurrency control is designed in Phase 1.

## 5. Compatibility and performance guardrails

The recently measured real Mercury avatar turn reached the browser's first text around **2.04 seconds after STT completion** after the embedding batch fix (previously about 9.01 seconds). The memory embedding preparation stage was about **0.87 seconds** (previously 7.63 seconds with sequential calls). These values are a regression reference, not a hard constant latency SLA.

- No extra synchronous LLM or embedding provider calls without explicit authorization.
- No silent switch in prompt content, ordering, model-selection behavior, provider reasoning controls, or trace representations.
- No blocking on memory consolidation, reflection or TTS before returning first streamed text.
- Preserve user/companion/session/room isolation and current persistence semantics.
- Targeted comparative tests should measure time to first visible token and number of external calls; avoid broad benchmarking or full-suite invocation unless needed.
- No new dependencies, queue systems, database tables, or unrelated architecture changes in Phase 0.

## 6. Phase 0 acceptance matrix

| Test / evidence | Required outcome |
|---|---|
| Same context fixture through standard, SSE, spoken paths | Identical effective identity, memory ordering, room constraints, role structure, and provider prompt inputs. |
| Memory preparation with N missing embeddings | Existing bounded batch behavior, no N sequential remote requests. |
| Same completed logical turn | Same durable messages and same explicitly enabled post-turn effects, with no duplicate side effects. |
| Interrupted/cancelled turn | Correct partial/complete persistence semantics; no reflection as completed experience unless explicitly designed. |
| Provider response delivery | Normal JSON remains JSON; SSE remains incremental; spoken text streams to existing speech path. |
| Prompt/trace inspection | Inspected system/user/history/memory content corresponds to actual provider request after transformations. |
| Multi-session/room/user cases | Existing authorization, room isolation, and companion ownership preserved. |
| Realistic latency fixture | No measured degradation attributable to new preparation/finalization code or extra remote calls. |
| Cross-mode regression | Existing avatar interruption and playback correctness remain intact. |

**Definition of Phase 0 complete:** shared behavior is implemented once, real duplicated logic is eliminated, and the matrix passes. Merely calling a common helper while keeping competing context and persistence implementations does **not** satisfy completion.

## 7. Downstream integration contracts (reserved, not implemented in Phase 0)

### Persistent state (Phase 1)

- Separate **seed persona** from **evolved companion identity** and **user-companion relationship**; session only stores temporary conversational state.
- Preserve user-owned companion semantics reported by audit (one owning user per companion), unless explicitly changed.
- Give later phases stable reads and atomic writes to evolved identity without overwriting initial user-configured identity.
- Apply schema migration and compatibility strategy only after the Phase 1 model design is approved.

### Semantic memory and background processing (Phase 2)

- Distinguish user facts, episodes/shared events, companion self-beliefs/opinions, relationship state, and raw conversation history.
- Prefer using existing `messages`, `semantic_facts`, `episodes`, `timeline_entries`, `ConsolidationWorker` where justified; do not create duplicate stores by naming alone.
- Provide **durable** per-turn work records if current `asyncio.Queue` cannot survive restart; prefer PostgreSQL, already in stack, without introducing an orchestration dependency.
- Batch semantic extraction; use existing provider abstractions; select extraction model/cost limits with user approval.
- All state changes carry enough source provenance for correction; avoid invented experiential history being promoted as observed fact.

### Experience-driven evolution (Phase 3)

- Changes derive from episodes/meaning, not pseudo-random `random.uniform()` trait drift.
- Preserve identity anchors while allowing gradual change and earned opinions; relationship-specific changes live in relationship scope.
- Separate current simulated mood from durable emotional associations and long-term self-understanding.

### Behavioral personality (Phase 4)

- Use **identity -> character interpretation -> behavioral inclination -> natural dialogue** as a *responsibility model*, **not** automatically four separate stages or model calls.
- Explicitly include competing motivations, selective attention and private interpretation (E02/E03/E10).
- Default to current LLM inference with compact context or structured output where streaming can be preserved; extra inference needs approval and measured benefit.

### Emotion, expression, speech (Phases 5–6)

- Simulated emotional state and characteristic voice/delivery are provider-independent.
- Keep visible chat and spoken text clean; optional structured per-segment performance metadata only if the chosen model and TTS adapter can support it.
- Verify actual expressive controls of Fish Audio Drama 3 or other target TTS engine before designing engine-specific fields.

### Evaluation (all phases, formalized in Phase 7)

- Behavioral evaluations start early. Reuse the original comparison proposal: three strongly different companions, a shared scenario set (~30 prompts), and score distinctiveness/continuity without leaking persona names.
- Measure memory precision, factual corrections, scene isolation, identity survival across sessions, latency, and bounded external cost.

## 8. Decisions requiring explicit review before implementation

| Decision | Recommendation / boundary | When |
|---|---|---|
| Phase 0 shared-code shape | Extract actual common responsibilities from existing pipeline; **do not** invent replacement framework. | After direct code map, before task 0.2 |
| Legacy extraction activation policy | Consistent post-turn hook with explicit eligibility; no new sync model calls or retroactive backfill. | During Phase 0 task design |
| Long-lived identity ownership | Durable companion identity plus user-companion relationship where product semantics require it; keep original session as separate lifecycle. | Phase 1 |
| Background durability | Existing PostgreSQL-backed work state preferred if needed; no Celery/new Redis queue dependency without approval. | Phase 2 |
| Background extraction model | Assess existing provider capabilities, batch cost, precision and local-model availability; get user approval on provider/model limits. | Phase 2 |
| Memory decay / forgetting | Decide eligibility and summarization versus permanent deletion; preserve original user data by default. | Phase 2 |
| Expressive output protocol | Prefer typed dialogue/performance metadata instead of visible inline tags if compatible with streaming/TTS. | Phase 6 |
| Experimental features marked REVIEW | Each item gets explicit IN/DEFER/OUT decision with user, not implicit rejection. | Relevant phase |

## 9. What this design intentionally does not do

This document does **not** authorize a rewrite, migrations, speculative governance/behavior restrictions, new model calls, new speech technologies, or a wholesale prompt architecture redesign. It reserves **one shared conversation lifecycle** and defines the interfaces the later personality work must be able to reuse.
