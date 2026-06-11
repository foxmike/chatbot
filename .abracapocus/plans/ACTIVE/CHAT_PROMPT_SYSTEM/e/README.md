# e

## Goal

Build context and scene intelligence features. Create a dynamic token budget calculator that allocates context window across persona, memory, history, and scene based on model window size and content relevance. Wire token budgeting into the prompt layer system. Add relationship-stage-aware prompting that adapts prompt framing based on relationship depth (new acquaintance vs established bond vs intimate). Create an attention anchoring system that tracks and injects unresolved questions, companion promises, and active user goals. Add a long-term callback planning mechanism so companions can flag topics to raise later. Add contextual short response permission logic.

## Notes

- Async: Wave 1: e.1 (token_budget.py), e.3 (relationship.py), e.4 (attention_anchor.py), e.5 (callback_planner.py) — all disjoint. e.6 can run in Wave 1 since it creates new logic in steps.py not touching token budget or attention files. Wave 2: e.2 (prompt_layers.py) depends on e.1 for budget integration and must also account for e.4 attention anchoring output format.
- Risk: prompt_layers.py is modified in phases c and d — phase e modifications must be additive. relationship.py already exists; modifications must preserve existing RelationshipConfig behavior.
