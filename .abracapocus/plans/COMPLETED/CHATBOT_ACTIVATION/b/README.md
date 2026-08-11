# b

## Goal

Activate the memory system end-to-end: generate and store pgvector embeddings for every user and assistant message, wire the ContextCompiler into the chat pipeline so relevant past memories are retrieved and included in the LLM context window, configure the consolidation worker with the default LLM provider so background memory consolidation actually runs, wire enqueue_consolidation to trigger after conversations reach a threshold length, and connect Redis as the working memory cache for active conversations.

## Notes

- Async: Wave 1: update chatbot/memory/embedding.py (embedding generation function) and chatbot/config.py (Redis connection pool). Disjoint. Wave 2: update chatbot/core/turn.py (ExtractMemoryStep) and chatbot/memory/context_compiler.py (accept db session and vector store). Disjoint. Wave 3: update chatbot/api/chat.py (call embeddings + context compiler). Wave 4: update chatbot/api/streaming.py (same pattern, serialized after chat.py). Wave 5: update chatbot/consolidation/worker.py and chatbot/perf/handlers.py (disjoint from chat endpoints).
- Risk: chat.py and streaming.py modified again after phase A; phase ordering provides serialization. Redis connection must fail gracefully if Redis is unavailable. Embedding generation should not block the response.
