# c

## Goal

Wire embeddings, semantic retrieval, extraction, consolidation, and memory controls into the active runtime.

This phase MUST produce exactly 9 tasks as specified below:

Task c.1: Add embedding provider config — add embedding_provider and embedding_model settings to chatbot/config.py. Allowed write paths: chatbot/config.py. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task c.2: Initialize retrieval_engine in app lifespan — create and inject retrieval engine into FastAPI app state. Allowed write paths: chatbot/api/app.py, chatbot/memory/retrieval.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task c.3: Wire pgvector retrieval into pipeline — connect retrieval_engine to retrieve step in chatbot/core/turn.py. Allowed write paths: chatbot/core/turn.py, chatbot/memory/retrieval.py. selected_backend: codex_cli

Task c.4: Verify extraction runs via background worker — ensure memory_extract handler processes extraction. Allowed write paths: chatbot/perf/handlers.py, chatbot/memory/extraction.py, docs/verification/phase_c_memory_extract_worker.md. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task c.5: Verify consolidation runs via worker — ensure consolidate handler runs summarization, contradiction, decay. Allowed write paths: chatbot/perf/handlers.py, chatbot/consolidation/summarizer.py, chatbot/consolidation/contradiction.py, chatbot/consolidation/decay.py, docs/verification/phase_c_consolidation_worker.md. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task c.6: Mount MemoryPanel in app shell — wire MemoryPanel component into settings/panel area. Allowed write paths: react_ui/src/App.tsx, react_ui/src/components/MemoryPanel.tsx. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task c.7: Mount HistorySearch in app shell — wire HistorySearch component. Allowed write paths: react_ui/src/App.tsx, react_ui/src/components/HistorySearch.tsx. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task c.8: Verify provenance and memory controls — test pin/delete/correct/privacy controls end-to-end. Allowed write paths: tests/test_memory_management.py, docs/verification/phase_c_memory_controls.md. selected_backend: pi_cli, model: openrouter/qwen/qwen3-235b-a22b-thinking-2507

Task c.9: Write memory integration tests — create tests/test_memory_integration.py. Allowed write paths: tests/test_memory_integration.py. selected_backend: pi_cli, model: openrouter/qwen/qwen3-235b-a22b-thinking-2507

## Notes

- Async: c.1, c.2, c.6, c.7 can run in parallel. c.3 depends on c.2. c.4 and c.5 depend on phase_b. c.8 and c.9 depend on c.3.
- Risk: Incorrect embedding config silently degrades retrieval. Missing scoping can leak semantic facts between users.
