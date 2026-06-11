# c

## Goal

Build memory-aware prompting features on top of the retrieval engine wired in phase a and the prompt layer system built in phase b. Create multi-timescale memory structuring that separates retrieved memories into recent conversational state, stable identity facts, long-term narrative arcs, and active goals. Add retrieval confidence scoring so low-relevance memories are dropped. Add contradiction-aware recency tagging. Add imperfect memory realism directives. Add narrative gravity weighting for emotionally salient events. Create a session-resume delta injection block.

## Notes

- Async: Wave 1: c.1 (timescale.py), c.2 (confidence.py), c.5 (narrative_gravity.py), c.6 (session_delta.py) — all new files, disjoint. Wave 2: c.3 (retrieval.py) depends on c.1 and c.2 to integrate scoring and timescale structuring. c.4 (prompt_layers.py) depends on c.1 for timescale-aware injection format and can run in parallel with c.3 since different files.
- Risk: retrieval.py modifications must not break existing retrieval path when embeddings are absent. Confidence scoring must gracefully pass through all memories when scoring is unavailable.
