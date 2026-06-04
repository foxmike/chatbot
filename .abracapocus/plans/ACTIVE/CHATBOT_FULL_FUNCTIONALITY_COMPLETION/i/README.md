# i

## Goal

Make admin dashboard, traces, usage tracking, model routing, streaming chat, cache, and performance controls usable.

This phase MUST produce exactly 11 tasks as specified below:

Task i.1: Gate admin routes with admin auth. Allowed write paths: chatbot/api/admin.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task i.2: Decide and implement observability persistence to DB. Allowed write paths: chatbot/observability/tracing.py, chatbot/observability/metrics.py, chatbot/db/models.py, docs/verification/phase_i_observability_persistence_decision.md. selected_backend: codex_cli

Task i.3: Wire collectors to DB tables. Allowed write paths: chatbot/observability/tracing.py, chatbot/observability/metrics.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task i.4: Configure task-class model routing. Allowed write paths: chatbot/providers/router.py, chatbot/config.py. selected_backend: codex_cli

Task i.5: Secure streaming endpoint with auth. Allowed write paths: chatbot/api/streaming.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task i.6: Add history and persistence to streaming. Allowed write paths: chatbot/api/streaming.py, chatbot/core/turn.py. selected_backend: codex_cli

Task i.7: Mount streaming chat option in App.tsx. Allowed write paths: react_ui/src/App.tsx. selected_backend: codex_cli

Task i.8: Mount AdminDashboard behind admin gate. Allowed write paths: react_ui/src/App.tsx, react_ui/src/components/AdminDashboard.tsx. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task i.9: Decide Redis use for cache/queue. Allowed write paths: chatbot/perf/cache.py, chatbot/perf/background.py, docker-compose.yml, docs/verification/phase_i_redis_cache_queue_decision.md. selected_backend: antigravity_cli

Task i.10: Add health/readiness endpoints. Allowed write paths: chatbot/api/health.py, chatbot/api/app.py. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task i.11: Write streaming and admin integration tests. Allowed write paths: tests/test_streaming_integration.py, tests/test_admin_integration.py. selected_backend: pi_cli, model: openrouter/qwen/qwen3-235b-a22b-thinking-2507

## Notes

- Async: i.1, i.4, i.5, i.9, i.10 in parallel. i.2 then i.3. i.6 depends on i.5. i.7 depends on i.6. i.8 depends on i.1. i.11 depends on i.6 and i.8.
- Risk: Public admin endpoints are a security issue. Streaming without persistence diverges from durable chat history.
