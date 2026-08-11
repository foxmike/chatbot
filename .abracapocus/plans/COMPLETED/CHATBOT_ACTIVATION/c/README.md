# c

## Goal

Activate identity drift and mood updates in the chat pipeline. After each assistant response, run identity evolution logic to calculate slow drift updates to personality traits based on conversation patterns, calculate mood updates based on conversation sentiment, persist updated values back to IdentityStateModel, and feed updated mood into the next turn's prompt assembly. Currently evolution.py is only imported for session seeding; it is never called during chat execution.

## Notes

- Async: Wave 1: update chatbot/soul/evolution.py (expose calculate_drift and calculate_mood interface). Wave 2: update chatbot/core/turn.py (add post-response identity update step, depends on wave 1). Wave 3: update chatbot/api/chat.py (invoke post-response identity update). Wave 4: update chatbot/api/streaming.py (same pattern, serialized after chat.py).
- Risk: turn.py is modified again; phase ordering serializes. Evolution calculations should be lightweight and not block the response.
