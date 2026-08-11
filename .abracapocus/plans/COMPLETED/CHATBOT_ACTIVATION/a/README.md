# a

## Goal

Wire the persona and system prompt pipeline so that every LLM call includes the companion's character profile (name, backstory, personality sliders, humor style, communication style), the session's identity state (fixed traits, slow drift values, current mood), and the session's relationship config (directness, warmth, initiative). Currently chat.py uses an empty fallback persona, streaming.py hardcodes persona={}, and turn.py's _assemble_persona_prompt never calls CharacterProfile.to_prompt_fragment() or forwards identity state.

## Notes

- Async: Wave 1: update chatbot/soul/persona_prompt.py. Wave 2: update chatbot/core/turn.py (depends on wave 1 interface). Wave 3: update chatbot/api/chat.py (depends on wave 2). Wave 4: update chatbot/api/streaming.py (depends on wave 2, serialized after chat.py).
- Risk: chat.py and streaming.py are the two main request handlers; changes must preserve existing message persistence and streaming behavior. turn.py is a large shared file; edits must be surgical to _assemble_persona_prompt only.
