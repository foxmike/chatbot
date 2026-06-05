# Phase I Observability Persistence Decision

Task: i.2 - Decide and implement observability persistence to DB

## Decision

Observability traces and usage metrics now persist to the existing migration-backed
database tables:

- `observability_traces`
- `observability_usage`

The module-level collectors default to DB-backed persistence via the existing
`chatbot.db.session.async_session_factory`. The in-memory collections remain as a
fallback/read cache so observability failures do not fail chat turns or admin
reads.

No schema change was introduced. The implementation uses the existing ORM models
and migration-backed table shape in `chatbot.db.models`.

## Implementation Notes

- `TraceCollector.complete_trace()` persists completed trace records to
  `observability_traces`.
- `TraceCollector.get_trace()`, `list_traces()`, and `flush()` can read or flush
  persisted trace records when DB access is available.
- `MetricsCollector.record_trace()` upserts aggregate usage into
  `observability_usage`.
- Metrics reads prefer DB-backed usage/trace data and fall back to in-memory
  aggregates if DB access fails.
- DB errors are logged and swallowed by the collectors, preserving the turn
  pipeline behavior.

## Verification

Command:

```bash
python -m py_compile chatbot/observability/tracing.py chatbot/observability/metrics.py chatbot/db/models.py
```

Result: PASS

Additional check:

```bash
git diff --check -- chatbot/observability/tracing.py chatbot/observability/metrics.py chatbot/db/models.py docs/verification/phase_i_observability_persistence_decision.md
```

Result: PASS

## Phase Risk Assessment

The task outcome does not introduce the phase risk noted in the skeleton:

- No new dependency, framework, table, or migration was added.
- Persistence uses existing observability tables and ORM mappings.
- Observability DB failures are non-fatal and fall back to process memory.
- The change is scoped to the allowed observability persistence files and this
  verification artifact.
