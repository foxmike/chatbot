# a

## Goal

Voice. A TTS/STT provider interface mirroring the LLM Provider pattern. A TTS adapter with emotional cadence mapped from phase-K mood and mode. An STT adapter for voice input. A call/phone mode. API endpoints for synthesize and transcribe, plus UI voice controls and a recorder hook. Voice preferences persist for continuity.

## Notes

- Async: Wave 1 (disjoint): voice/__init__.py + base.py as one package task, cadence.py (maps mood/mode to voice params), and db/models.py voice-preference fields. Wave 2: tts.py (depends on base.py), stt.py (depends on base.py), the 0009 migration (depends on models.py), client.ts audio extension, and useAudioRecorder.ts hook. Wave 3: api/voice.py endpoints (depends on tts, stt, cadence), api/app.py registering the voice router (single-owner, depends on voice.py), and the VoiceControls and CallMode React components (depend on client.ts and the recorder hook). Test fan-in last.
- Risk: Voice TTS/STT model identifiers must be validated like LLM backend/model pairs before execution. db/models.py and api/app.py single-owner. The 0009 migration must be reversible with its real head revision verified at materialize time. Streaming call mode is in scope only if the operator confirms; otherwise build async voice-message synthesis.
