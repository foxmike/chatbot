# f

## Goal

Consolidate broken Alembic migrations into one clean initial migration, remove the create_all() startup race condition, add auto-generated session/room titles from conversation content, and configure voice system TTS/STT providers. Delete all existing broken migration files in alembic/versions/. Generate one clean initial migration from current models. Verify make reset-db && make migrate works on fresh database. Remove or guard create_all() in app startup. Add post-conversation hook for LLM-generated title. Wire TTS_API_KEY and STT_API_KEY env vars into voice adapters.

## Notes

- Async: Wave 1: clean alembic/versions/ and generate initial migration. Update chatbot/api/app.py (remove create_all). Update chatbot/voice/tts.py and chatbot/voice/stt.py (wire API keys). Update .env.example. All disjoint. Wave 2: update chatbot/api/chat.py (auto-title generation). Wave 3: update chatbot/api/streaming.py (auto-title, serialized after chat.py).
- Risk: Migration consolidation is destructive — existing databases need make reset-db. Voice API keys may not be available; adapters should fail clearly when unconfigured.
