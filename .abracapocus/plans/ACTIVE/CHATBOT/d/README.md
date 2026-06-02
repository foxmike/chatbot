# d

## Goal

Memory core — the first differentiator. Working-memory buffer, episodic store, retrieval over the VectorStore with relational filtering, and the context compiler that assembles bounded, ranked context for each turn (not naive top-k).

## Notes

- Async: working_memory.py and episodic.py run in parallel (wave 1, disjoint). retrieval.py depends on the store + episodic (wave 2). context_compiler.py depends on retrieval (wave 3). tests fan in.
- Risk: The context compiler is a core differentiator; route it to a strong profile. Keep retrieval logic first-party and deterministic so behavior is reproducible.
