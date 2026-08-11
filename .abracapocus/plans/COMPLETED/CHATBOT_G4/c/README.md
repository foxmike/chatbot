# c

## Goal

Observability and intelligent routing. Per-turn tracing (prompt, retrieval, model routing, latency). Structured logs for memory-write, contradiction, and failed-retrieval events. Latency, token, and cost metering per conversation and per model. An admin dashboard. A task-class-aware extension of the provider policy router (casual, emotional, summarize, extract, embed) so cheap models handle casual turns and stronger models handle nuance.

## Notes

- Async: Wave 1 (disjoint): observability/__init__.py + tracing.py as one package task, metrics.py, logs.py, router.py task-class routing extension, and db/models.py trace/usage tables. Wave 2: the 0011 migration (depends on models.py), api/admin.py endpoints (depends on tracing/metrics/logs), chatbot/core/turn.py emitting traces per step (single-owner, depends on tracing.py), and client.ts extension. Wave 3: api/app.py registering the admin router (single-owner, depends on admin.py), and the AdminDashboard React component (depends on client.ts). Test fan-in last.
- Risk: router.py and turn.py are core; each is single-owner and changes must be additive. Task-class routing should default safe (single model) unless the operator enables automatic routing, and any new routed model identifiers must be validated. Choose the trace/metric store per the operator answer to avoid loading the primary database. The 0011 migration must be reversible with its real head revision verified at materialize time.
