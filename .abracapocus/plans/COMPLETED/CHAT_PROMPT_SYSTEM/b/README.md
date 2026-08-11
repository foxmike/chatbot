# b

## Goal

Build prompt architecture foundation: create a deterministic prompt layer ordering module that composes system prompts from prioritized blocks (safety floor, platform behavior, companion identity, relationship stage, room/scene, mood/state, memory, session goals), create a per-model prompt formatter that selects formatting strategy by model family, create an intent classifier that categorizes user messages before prompt assembly, add prompt segment hashing to observability traces, and wire both new modules into the compile_context and call_provider pipeline steps.

## Notes

- Async: Wave 1: b.1 (prompt_layers.py), b.2 (prompt_formatter.py), b.3 (intent_classifier.py) — all new files, fully disjoint. Wave 2: b.4 (observability/traces.py) can run in parallel with Wave 1. Wave 3: b.5 (turn.py + steps.py wiring) depends on b.1, b.2, b.3. b.6 depends on b.5 — serialize b.5 before b.6 since both may touch turn.py.
- Risk: turn.py is the pipeline core — wiring must preserve existing step execution order. Prompt formatter must gracefully fall back to current behavior for unknown model families.
