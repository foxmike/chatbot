# Companion Evolution — Phase 2.3 Memory Lifecycle Design

> This document supplements the approved Phase 2 architecture in `COMPANION_PHASE2_DESIGN.md`. It specifies the remaining Phase 2.3 memory lifecycle design; it does not replace or approve unresolved product decisions.

**Status:** Supplementary implementation design; no production changes authorized by this document.  
**Authorities:** `COMPANION_EVOLUTION_REQUIREMENTS.md`, `COMPANION_EVOLUTION_ARCHITECTURE.md`, and the Phase 2 roadmap in `COMPANION_EVOLUTION_IMPLEMENTATION.md`.  
**Scope:** Consolidation, contradiction/supersession, and selective forgetting under Task 2.3. The preceding Phase 2 contracts are retained as context; the task boundaries follow the authoritative roadmap.

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

## Task 2.3a — Consolidation, reconciliation, and selective forgetting

**Status:** Implementation design; no production changes authorized here.  
**Requirements:** A05, A07, A08, A09, A10, A16, P10; P11 remains REVIEW in the requirements register.  
**Prerequisite:** durable job repository and worker from Phase 2.2 must be available. This task uses that infrastructure and must not create another queue/scheduler.

### Existing components and observed gaps

Reuse `MemoryExtractor`/`SQLAlchemyMemoryStorage` for source-linked semantic facts and episodes; `ConsolidationWorker` as the bounded orchestration entry point; `Summarizer` for batched summaries; `ContradictionResolver` for pairwise comparison; `MemoryDecayEngine` only after replacing its delete-oriented use with retrieval eligibility; `RetrievalEngine`/`PgvectorStore` for filtering eligible facts; and SQLAlchemy/Alembic conventions in `chatbot/db` and `migrations/versions`.

Current `ConsolidationWorker.run_consolidation` fetches bounded recent entries, resolves pairs, summarizes unsummarized entries, then calls `MemoryDecayEngine.prune` and `_persist_pruned_entries`; the DB fallback physically deletes entries. It writes long-term summaries as new `SemanticFact`s without a durable link to all their source records. Its coalescing timestamp and lock are process-local fallbacks, so they are not cross-worker idempotency controls. `ContradictionResolver` has deterministic rules plus an optional provider call and an in-memory cache; its result is currently applied by adapter-specific store methods without a common durable supersession transition. `MemoryDecayEngine` reads relevance/importance/access values from metadata and defaults missing relevance to `1.0`; these fields are not defined on `SemanticFact`. `SemanticFact` stores user owner, one optional source message, source type/turn, confidence, privacy and pinned; it has no lifecycle/supersession/access state or companion/room scope. `Episode` stores session and message bounds but no status or lineage. Message/session provenance carries owner, companion and room only when present. Preserve the 2.4a rule: derive trusted ownership through session and never infer missing scope.

### Lifecycle model

Treat lifecycle transitions as durable state changes, not deleting source evidence:

- **Active:** eligible for retrieval; source and scope checks still apply.
- **Consolidated:** an active canonical memory represents one or more equivalent source memories. Keep originals and provenance; mark duplicate sources as `consolidated` and point/link them to the canonical target. Do not merge records across different owner, companion, room, attribution, time context, or memory type.
- **Superseded:** a newer/corrected memory replaces a prior value for the same subject and compatible scope. Preserve the old row and evidence, set its status and a link to the replacement, and remove it from ordinary retrieval. A correction is a supersession with explicit correction evidence; a time-varying value (job, residence, preference) is a temporal succession, not a contradiction. Two differently scoped or temporally qualified claims are not contradictory. For a true unresolved conflict, keep both active, record conflict linkage, and do not choose a winner solely by recency. A resolver/provider result is advisory unless confidence and evidence meet a deterministic acceptance rule; ambiguous results remain unresolved.
- **Suppressed:** below retrieval eligibility after decay, without deleting or rewriting its source. Reinforcement/importance/recency/relevance can change eligibility only when values exist; missing values use neutral defaults and are not fabricated. Pinned memories remain eligible absent explicit deletion/correction.
- **Deleted:** only on an explicit authorized deletion request or existing owner/session deletion policy. Delete the targeted memory and its derived embeddings/lineage as required by privacy policy, but never cascade into raw conversation history unless a separate explicit conversation deletion is requested. Superseded/consolidated is not physical deletion.

Consolidation candidates are drawn in bounded batches within an identical trusted ownership/scope and memory type. Use existing normalized text/entity and provenance fields for deterministic exact/near duplicate prefiltering, then the existing summarizer/resolver for ambiguous candidates in bounded batches, off the conversation path. Preserve distinguishing details (time, polarity, attribution, uncertainty, episode participants and commitments). Persist canonical result, lineage links, and lifecycle transitions atomically. Deterministic keys over sorted source IDs + operation version make job retries idempotent. If provider/model work is repeated after a crash, the persistence key and transaction prevent duplicate canonical rows or transitions.

### Minimal data changes

Existing columns are insufficient for durable memory lifecycle and multi-source provenance. Add one Alembic revision only when implementing this design, with:

1. A lifecycle status on semantic facts and episodes (`active`, `consolidated`, `superseded`, `suppressed`; default `active`), nullable target ID for same-table canonical/superseding link, and transition timestamp/reason. Keep original source columns unchanged.
2. A normalized `memory_lineage` table linking source fact/episode/message IDs to a target fact/episode ID, relation (`supports`, `duplicate_of`, `supersedes`, `conflicts_with`), and creation time. Enforce one source and one target per row, foreign keys, uniqueness on source/target/relation, and owner/scope validation in repository transactions. This retains all evidence when summaries combine many records; avoid polymorphic unvalidated IDs by using nullable typed FK columns with a one-of check.
3. Nullable explicitly sourced importance/reinforcement/access fields only if Task 2.3 extraction and retrieval can populate them from observable events. Prefer existing confidence and pinned fields where their semantics match. Do not backfill invented importance. `last_accessed_at`/`access_count` updates are optional for this task and should be added only if actual retrieval accesses can be recorded without adding synchronous work; otherwise recency is based on existing timestamps.

Companion and room scope representation is a prerequisite from the Phase 2 retrieval/storage integration: do not consolidate or transition candidates when scope cannot be proven equal. User-level shared facts remain a distinct scope. A memory lacking reliable provenance is excluded from cross-scope consolidation and automatic supersession, but remains available under its existing user ownership rules until explicit deletion.

### Background execution and transaction behavior

Schedule bounded `memory_consolidate` work through the durable Phase 2 job table, with deterministic identity per owner/scope and source batch/version. Coalesce work by unique job identity; cap candidate count, prompt size, provider attempts and per-run operations. Worker loads candidates using owner/scope filters, does comparison/summarization off-transaction, then revalidates source versions/status and atomically writes canonical memory, lineage, and transitions. No extraction/consolidation/provider call occurs before the interactive response or before successful turn persistence.

On timeout/provider/database failure, leave source rows unchanged and let the durable worker retry with bounded backoff. A retry rechecks candidate status/version and idempotency key. If another worker already completed or superseded the same inputs, mark the stale job successful/no-op. After finite attempts, record terminal failure and retain source memories untouched. Never report success before all canonical rows, links, and transitions commit together.

### Acceptance tests

Use real SQLAlchemy sessions and isolated PostgreSQL for transaction/locking behavior; fake provider responses only where provider output is under test.

- Duplicate facts consolidate to one active canonical fact; each original remains addressable and has correct lineage. Similar wording with different scope, attribution, polarity, or time stays distinct.
- Same-subject opposite values classify as correction, temporal change, unresolved conflict, or separate-context pair according to explicit evidence; only accepted correction/temporal succession supersedes. Unresolved conflicts retain both active.
- Correction provenance points to the correcting source; old value remains stored and ordinary retrieval excludes it after commit. Rollback leaves original active and creates no partial lineage.
- Decay thresholds suppress only when observable age/relevance/reinforcement inputs justify it; explicit importance and pinned behavior are covered. Missing signals do not cause invented ranking. Suppression is reversible; explicit delete removes only requested memory/derived artifacts and leaves messages intact.
- Replaying the same durable job, crashing after canonical insert, and running concurrent workers produce one canonical result and complete lineage; stale candidates become safe no-ops; terminal failures are visible.
- Cross-user/companion/room candidates never share a consolidation, contradiction, or supersession operation. Shared user facts remain in their shared scope. Missing scope provenance is not guessed.
- Retrieval excludes consolidated duplicates, superseded and suppressed rows, retains active conflicts when policy allows, honors existing limits/token budgets, and does not add an embedding or LLM call to the interactive path.

### Small coding tasks

- **2.3a1 — Lifecycle schema and repository:** migrations/model/repository for statuses, lineage and atomic transitions; PostgreSQL rollback, uniqueness and ownership tests. No provider or worker changes.
- **2.3a2 — Deterministic duplicate identification (implemented component: `chatbot/consolidation/duplicates.py`):** group exact normalized assertions only when owner and available scope match; return stable canonical/source IDs without mutating evidence. Follow with a separately reviewed integration that classifies correction/temporal/conflict/context cases and persists provenance-preserving transitions.
- **2.3a3 — Durable consolidation handler:** bounded jobs through the existing durable worker; existing summarizer/resolver, idempotent writes, bounded retry/failure behavior, concurrency and crash-replay tests.
- **2.3a4 — Selective retrieval eligibility and explicit deletion:** replace physical decay pruning with durable suppression eligibility, add explicit memory deletion path under existing authorization, and test retrieval/token limits plus preservation of raw messages.

**Architecture conflicts to resolve before coding:** P11 is marked REVIEW, so product approval is required before enabling automatic suppression thresholds or irreversible memory deletion. Existing `MemoryDecayEngine.prune` physically removes entries, contrary to the required provenance-preserving lifecycle; it must not be used unchanged. Semantic facts lack explicit companion/room scope and multi-source lineage; implement the smallest scope/provenance migration first, in coordination with storage/retrieval work. No other scheduler, framework, or conversation-path changes are needed.
