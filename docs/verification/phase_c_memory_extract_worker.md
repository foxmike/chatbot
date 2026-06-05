# Phase C Verification: Memory Extract Background Worker

**Task**: c.4 — Verify extraction runs via background worker
**Date**: 2026-06-04
**Status**: ✅ PASSED

---

## 1. Summary

Verified that the `memory_extract_handler` correctly processes extraction when dispatched
by `BackgroundWorker`. The full pipeline was tested:

```
BackgroundWorker._dispatch()
  → memory_extract_handler (chatbot/perf/handlers.py)
    → MemoryExtractor.extract (chatbot/memory/extraction.py)
      → _extract_entries (heuristic fact/episode extraction)
      → SQLAlchemyMemoryStorage.persist (if database available)
```

## 2. Pipeline Architecture

### 2.1 Task Enqueuing

`TurnPipeline.enqueue_deferred()` in `chatbot/core/turn.py` creates `BackgroundTask` objects
for the `"extract_memory"` deferred step. The task type is mapped to `"memory_extract"` via
`_background_task_type()`.

### 2.2 Worker Registration

`chatbot/api/app.py` wires the handler at application startup:

```python
worker = await start_worker(
    _background_queue,
    handlers={
        "memory_extract": _memory_extract_handler,
        "consolidate": consolidate_handler,
    },
)
```

The app-level `_memory_extract_handler` wrapper injects `database=async_session_factory`
into the payload and delegates to the core `memory_extract_handler`.

### 2.3 Handler (chatbot/perf/handlers.py)

`memory_extract_handler`:
- Parses `session_id`, `user_message`, `provider_response` from the payload
  (supports both top-level and `turn_context`-nested locations)
- Forwards `database` from payload/turn_context to `MemoryExtractor`
- Calls `extractor.extract(session_id, user_message, provider_response)`
- Raises `ValueError` for missing required fields
- Re-raises extraction exceptions after logging

### 2.4 Extractor (chatbot/memory/extraction.py)

`MemoryExtractor.extract()`:
- Runs `_extract_entries()` — heuristic regex-based fact and episode extraction
- Facts: detected from user messages (e.g., "I work at...", "my name is...") and
  assistant memory confirmations (e.g., "I'll remember that...")
- Episodes: one per turn, summarizing user + assistant content
- Persists via `SQLAlchemyMemoryStorage` if a database is available

## 3. Verification Tests (9/9 passed)

| # | Test | Result |
|---|------|--------|
| 1 | Handler parses `enqueue_memory_extraction` payload | ✅ PASSED |
| 2 | Handler raises `ValueError` on missing `session_id` | ✅ PASSED |
| 3 | Handler raises `ValueError` on missing `user_message` | ✅ PASSED |
| 4 | Handler reads `session_id` from `turn_context` fallback | ✅ PASSED |
| 5 | Handler calls `MemoryExtractor.extract` with correct arguments | ✅ PASSED |
| 6 | Handler forwards `database` to `MemoryExtractor` | ✅ PASSED |
| 7 | `BackgroundWorker` dispatches `memory_extract` task to handler | ✅ PASSED |
| 8 | Heuristic extraction produces expected facts and episode | ✅ PASSED |
| 9 | Handler re-raises extraction exceptions | ✅ PASSED |

Test file: `tests/test_verification_memory_extract_worker.py`

## 4. Compilation Check

```
$ python -m py_compile chatbot/perf/handlers.py    → OK
$ python -m py_compile chatbot/memory/extraction.py → OK
```

## 5. Payload Compatibility

The payload from `TurnPipeline._deferred_turn_payload()` is fully compatible with
`memory_extract_handler`:

| Field | Source (payload) | Source (turn_context) | Status |
|-------|-----------------|----------------------|--------|
| `session_id` | ✅ | ✅ | Both paths supported |
| `user_message` | ✅ | ✅ | Both paths supported |
| `provider_response` | ✅ | ✅ | Both paths supported |
| `database` | — | ✅ (injected by app wrapper) | Via turn_context |

## 6. Assessment

- ✅ **Extraction runs via background worker**: `BackgroundWorker._dispatch()` correctly
  routes `"memory_extract"` tasks to the handler
- ✅ **Handler processes extraction**: `memory_extract_handler` parses payload, creates
  `MemoryExtractor`, and calls `extract()`
- ✅ **Heuristic extraction works**: Facts and episodes are produced from user/assistant text
- ✅ **Error handling**: Missing fields raise `ValueError`; extraction failures are
  logged and re-raised; worker retries or drops exhausted tasks
- ✅ **No phase risk**: The pipeline follows the deferred-step pattern (fast path →
  enqueue → background worker → handler → extractor) without architectural deviation
