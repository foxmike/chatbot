# b

## Goal

Make the BackgroundQueue actually consume deferred tasks.

This phase MUST produce exactly 7 tasks as specified below:

Task b.1: Create background worker module — implement worker loop in chatbot/perf/worker.py that consumes from BackgroundQueue. Allowed write paths: chatbot/perf/worker.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task b.2: Define memory_extract task handler — create chatbot/perf/handlers.py with handler that calls memory extraction modules. Allowed write paths: chatbot/perf/handlers.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task b.3: Define consolidate task handler — add consolidation handler to chatbot/perf/handlers.py. Allowed write paths: chatbot/perf/handlers.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task b.4: Wire worker into FastAPI lifespan — start worker on startup, graceful shutdown in chatbot/api/app.py. Allowed write paths: chatbot/api/app.py, chatbot/perf/worker.py. selected_backend: codex_cli

Task b.5: Add worker health check endpoint — add /worker/health endpoint. Allowed write paths: chatbot/api/health.py, chatbot/api/app.py. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task b.6: Add Docker Compose worker service — add worker service to docker-compose.yml. Allowed write paths: docker-compose.yml. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task b.7: Write background worker tests — create tests/test_background_worker.py. Allowed write paths: tests/test_background_worker.py. selected_backend: pi_cli, model: openrouter/qwen/qwen3-235b-a22b-thinking-2507

## Notes

- Async: b.1, b.2, b.3 can run in parallel. b.4 depends on b.1, b.2, b.3. b.5 and b.6 are independent. b.7 depends on b.4.
- Risk: In-process workers can duplicate work under multi-process deployment. Queue contents lost on restart if not using Redis.
