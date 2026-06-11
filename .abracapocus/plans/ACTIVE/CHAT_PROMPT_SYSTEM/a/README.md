# a

## Goal

Activate memory infrastructure: configure embedding provider, wire retrieval_engine into TurnContext for both chat and streaming endpoints, add background worker service to docker-compose, wire consolidation worker with a default LLM provider, and wire structured_logger event emission into the pipeline so the admin Logs tab populates.

## Notes

- Async: Wave 1: a.1 (config.py), a.5 (docker-compose.yml), a.7 (observability/logs.py) — disjoint files. Wave 2: a.2 (bootstrap.py) depends on a.1. a.6 (perf/handlers.py + perf/worker.py) depends on a.5. Wave 3: a.3 (chat.py) and a.4 (streaming.py) depend on a.2 — disjoint files, can run in parallel. a.3 also touches turn.py so serialize a.3 before a.4 if both touch turn.py; if only a.3 touches turn.py they can parallelize.
- Risk: chat.py and streaming.py are critical path files. Retrieval wiring must not break existing chat flow when no embeddings exist yet. Consolidation worker must gracefully no-op when provider is unavailable.
