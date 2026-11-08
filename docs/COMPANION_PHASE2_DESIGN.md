# Companion Evolution — Phase 2 Design

**Status:** Task contracts recovered from the accepted roadmap and current code. No implementation authorized by this document.  
**Authorities:** `COMPANION_EVOLUTION_REQUIREMENTS.md`, `COMPANION_EVOLUTION_ARCHITECTURE.md`, and the Phase 2 roadmap in `COMPANION_EVOLUTION_IMPLEMENTATION.md`.  
**Scope:** Tasks 2.2–2.4 only. Task boundaries below preserve the roadmap; this document does not advance 2.3 or 2.4 into 2.2.

## Existing system

- `chatbot/memory/extraction.py` contains `MemoryExtractor`, heuristic fact/episode selection, and `SQLAlchemyMemoryStorage`. It writes to existing semantic fact and episode stores. The extractor currently uses regex-like rules and swallows persistence exceptions by returning an empty list; it is not a durable job system or semantic extractor.
- `chatbot/consolidation/worker.py` contains `ConsolidationWorker`, its pipeline and existing persistence adapters. `chatbot/perf/handlers.py` dispatches memory extraction and consolidation; `chatbot/perf/worker.py` is the asynchronous handler loop.
- `chatbot/perf/background.py` defines `BackgroundTask` and `BackgroundQueue`, an in-process `asyncio.Queue` with priority lanes and generated random IDs. Retries mutate an in-memory counter and re-enqueue; exhaustion or a missing handler drops the task. Process exit loses queued and retrying work.
- `chatbot/api/app.py` starts the worker during FastAPI lifespan and stops it at shutdown. It registers extraction and consolidation handlers and supplies `async_session_factory` to extraction.
- `chatbot/core/turn.py` has `ExtractMemoryStep` and `TurnPipeline.enqueue_deferred`. The normal pipeline can extract inline; its deferred path puts memory/consolidation task payloads in the volatile queue. The fast path returns to that deferred path. Actual turn eligibility must remain governed by shared completion/persistence outcome, not by pipeline entry alone.
- `chatbot/api/chat.py` runs the shared pipeline; `chatbot/api/streaming.py` owns SSE completion, persistence and disconnect/interruption handling; `chatbot/avatar/router.py` calls `stream_chat` for spoken turns. These shared lifecycle boundaries are the integration points; avoid transport-specific schedulers.
- `chatbot/db/models.py` uses SQLAlchemy declarative models, UUID keys, timezone-aware timestamps and foreign keys. Database access is async SQLAlchemy via `chatbot/db/session.py`; migrations are Alembic revisions. Existing `messages`, `semantic_facts`, `episodes`, timeline and companion tables remain authoritative stores and must be preserved.

## Task 2.2 — Durable completed-turn work dispatch and recovery

**Requirements:** A01, A06, A09, A10, A16. The job represents completed-turn extraction work; it does not define new extraction semantics. Reuse PostgreSQL, SQLAlchemy, `BackgroundTask`, existing worker/handler lifecycle, `MemoryExtractor`, and the shared completion contract. No new framework, dependency, synchronous inference or unbounded per-turn side jobs.

### Minimum persistence change

Add one Alembic-managed `background_jobs` table and SQLAlchemy model. Minimum columns: deterministic UUID/job key (primary key), task type, user ID, session ID, companion ID nullable for companion-less sessions, payload JSON, status (`pending`, `running`, `succeeded`, `failed`), attempt count, available-at, created/updated timestamps, claimed-at, completed-at, and bounded last-error text. Foreign keys scope user/session/companion consistently with existing ownership, with deletion behavior matching current session/companion policy. Add an index for claimable status/available-at ordering. No migration backfill is required: volatile pre-migration queue contents cannot be recovered, and existing conversation/memory rows are not rewritten.

Job identity is unique per persisted logical turn and work type (use stable persisted `turn_id`; otherwise derive from the persisted user/assistant message IDs). Enqueue with PostgreSQL conflict-safe insert. Store references/compact work parameters, not copied unbounded conversation history; worker loads the persisted turn messages. Preserve provenance by linking extracted entries to source messages/turn using existing fields where supported.

### Lifecycle and transaction contract

1. Only the authoritative completion hook schedules extraction after the turn's successful persistence eligibility is known. Insert the job in the same database transaction as the completed messages/effects where that lifecycle owns the transaction. A rollback leaves neither completed turn nor job. Interrupted, failed, or unsaved turns schedule no job. HTTP, SSE and spoken share this rule; spoken inherits it through `stream_chat`. Standard-chat fast path stays free of synchronous semantic extraction.
2. Extend/reuse the worker dispatch boundary so a worker claims a bounded batch of due jobs transactionally using PostgreSQL row locking with `SKIP LOCKED`; commit the claim and attempt increment before invoking handlers. Other workers cannot claim the same live row. An expired `running` claim becomes eligible after a fixed lease timeout, so process death is recoverable without distributed locks.
3. Run extraction outside the claim transaction. Persist memory outputs idempotently, keyed by source turn/message plus extraction kind (or equivalent database uniqueness), and mark success only after output persistence. If a crash occurs after writing results but before success, retry sees the uniqueness keys and completes without duplicate memories. Existing writes and completion status should share a transaction where the handler can own it; do not take ownership of an outer conversation transaction.
4. On handler error, return the row to pending with bounded exponential delay while attempts remain; after the configured finite attempt limit, persist `failed`, completion timestamp and last error. Never silently discard terminal work. Bound batch size, attempts, and payload size. Keep existing lifespan startup/shutdown, replacing volatile-only delivery for this work type; unrelated task types can retain current queue semantics unless explicitly migrated in scope.

### Three implementation assignments

- **2.2a — Schema and repository:** migration/model plus async SQLAlchemy enqueue, claim, lease recovery, retry scheduling and terminal status operations. Focused PostgreSQL tests for uniqueness, claim concurrency, rollback and restart-visible rows.
- **2.2b — Completion integration:** schedule deterministic extraction jobs at the shared successful completion boundary for standard, SSE and spoken paths. Preserve transaction ownership, interruption/disconnect handling, fast path and duplicate completion protection. Verify no synchronous extractor/provider call is added.
- **2.2c — Worker and handler:** wire bounded PostgreSQL polling/claiming into existing app lifespan worker and adapt extraction handler to load owned persisted messages and write idempotently. Test restart, retry, crash-after-write recovery, ownership/isolation and terminal failure.

## Task 2.3 — Semantic extraction and provenance-aware consolidation

**Requirements:** A05, A07, A08, P10, plus A09/A10/A16. This is the roadmap's semantic-extraction task; do not include it in 2.2. Replace brittle regex-only selection rather than running a second competing extractor. Use bounded batched off-path processing and existing provider abstractions only after extraction provider/model and cost limits are explicitly approved. No per-message synchronous LLM call.

Interpret persisted conversation evidence into distinct user facts, stable preferences, companion-specific information, relationship information, episodes, and temporary circumstances. Mark or exclude hypothetical, roleplay, uncertain and assistant-originated claims from real-world user facts unless explicitly grounded. Treat corrections as evidence that supersedes or invalidates prior claims while retaining provenance. Persist source user/assistant message IDs, turn and timestamps, attribution, type, confidence and correction/supersession linkage using existing tables/columns where adequate; propose only the smallest approved schema extension after auditing current constraints. Consolidation reuses `ConsolidationWorker`; it must preserve evidence and distinguish facts from episode summaries and interpretations. Acceptance evaluates realistic precision/recall, correction, temporal changes and conflicting evidence with no blocking inference.

## Task 2.4 — Retrieval, isolation and forgetting policy

**Requirements:** P11, A07–A10. Preserve this roadmap item as **DESIGN** until the query eligibility/compaction versus hard-deletion decision and vector metric/index are verified and approved. Do not silently change operators, ranking, pruning or delete existing records.

Inspect `chatbot/memory/retrieval.py`, current embedding/index schema, ownership joins, and room/scene filters. Specify separate eligibility and ranking for facts, episodes and other approved memory types; enforce user and companion ownership and room/scene isolation at query and persistence boundaries. Confirm pgvector operator semantics and index compatibility against the actual query before proposing changes. Set a bounded growth policy only after the explicit forgetting decision. Acceptance compares retrieval relevance to baseline, verifies cross-user/companion/room isolation, and demonstrates bounded growth without unapproved data deletion.

## Preserved requirements and boundaries

No new dependencies or orchestration framework; no speculative monitoring subsystem; no additional synchronous inference or first-token-path work; preserve existing embeddings batching, provider routing, message/memory/identity/relationship/timeline/ritual data and transaction ownership. Do not begin Phase 3 work or promote inferred experience into fabricated user history. Each task uses focused tests and isolated PostgreSQL for database-specific behavior; do not run the full suite or modify development/production data.

## Blockers and decisions

No blocker remains for implementing 2.2 as the three assignments above. Task 2.3's extraction provider/model and bounded cost require explicit approval per the existing 2.1 roadmap. Task 2.4 remains blocked on its already-recorded retrieval metric/index and forgetting-policy decisions; these are not prerequisites for 2.2. The current roadmap itself needs no reconciliation and is unchanged.
