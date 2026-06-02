# e

## Goal

Soul and state engine — the second differentiator. Personality configuration, a durable state model, identity-continuity logic that resists drift across long horizons, and deterministic persona-prompt assembly. Internally coded so the persona does not depend on the underlying model's defaults.

## Notes

- Async: personality.py and state.py run in parallel (wave 1, disjoint). identity.py depends on state (wave 2). persona_prompt.py depends on personality + identity (wave 3). May overlap phase d entirely since write paths are disjoint, but phase ordering keeps them as separate verification boundaries.
- Risk: Identity continuity is where chatbots usually fail. Route identity.py and persona_prompt.py strong. Keep state single-sourced in Postgres unless the operator opts into a file-backed layer.
