# c

## Goal

Persistence and memory schema. SQLAlchemy models and the 0001 migration for users, sessions, messages, episodes, semantic facts, and entities; the pgvector embedding column; a VectorStore interface with a pgvector adapter; and an EmbeddingProvider interface. This is the single source of truth for state.

## Notes

- Async: models.py + session.py land first (wave 1), the 0001 migration depends on models (wave 2). vector_store.py (interface) and embeddings.py (interface) run in parallel in wave 1; pgvector_store.py depends on vector_store.py + models. tests are the fan-in.
- Risk: Schema is foundational. Migration must be reversible. Embedding dimension must match the chosen embedding provider (see operator questions) or the pgvector column is wrong.
