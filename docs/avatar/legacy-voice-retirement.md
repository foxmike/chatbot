# Legacy voice inventory and retirement requirements

Scope: everything in chatbot that speaks or transcribes *outside* the new
avatar pipeline (Faster-Whisper STT + Qwen3-TTS, owned by Sonnet's media
engine work). Files owned by other agents were **not** modified; this
document is the precise retirement contract to execute once the new
pipeline is merged.

## Current STT

| Component | Location | Notes |
|---|---|---|
| Deepgram adapter | `chatbot/voice/stt.py::_call_deepgram_stt` | HTTP call to `settings.stt_base_url` (default `https://api.deepgram.com/v1`), model `nova-2`, needs `STT_API_KEY`. **Retire.** |
| OpenRouter/OpenAI-compatible STT | `chatbot/voice/stt.py::_call_openai_compatible_stt`, `_call_openrouter_stt` | Uses `OPENROUTER_API_KEY`. **Retire.** |
| `STTAdapter`, `STTProvider`, `VoiceTranscriptionRequest/Result` | `chatbot/voice/stt.py`, `chatbot/voice/base.py` | The whole provider-neutral adapter layer exists to serve the above. **Retire** unless the new pipeline wants to reuse the request/result dataclasses. |

## Current TTS

| Component | Location | Notes |
|---|---|---|
| Kokoro (OpenAI-compatible endpoint) | `chatbot/voice/tts.py::_call_openai_compatible_tts`, defaults `DEFAULT_TTS_PROVIDER="kokoro"`, `DEFAULT_TTS_MODEL="hexgrad/kokoro-82m"` | **Retire.** |
| ElevenLabs | `chatbot/voice/tts.py::_call_elevenlabs_tts` (and `TTS_PROVIDER=elevenlabs` config default) | **Retire.** |
| OpenRouter TTS | `chatbot/voice/tts.py::_call_openai_compatible_tts` (provider `openrouter`) | **Retire.** |
| `TTSAdapter`, `TTSProvider`, `VoiceSynthesisRequest/Result` | `chatbot/voice/tts.py`, `chatbot/voice/base.py` | **Retire** (same caveat as STT). |
| CadenceMapper / CadenceProfile | `chatbot/voice/cadence.py` | Mood/mode → pitch/speed/pause mapping for the old providers. Qwen3-TTS uses voice-design instructions instead. **Retire.** |
| Prompt adapter | `chatbot/voice/prompt_adapter.py` | Feeds cadence hints into prompts. **Retire with the cadence layer.** |

## API routes to retire (`chatbot/api/voice.py`, `voice_router` mounted in `chatbot/api/app.py`)

* `POST /api/voice/synthesize` — drives `TTSAdapter`.
* `POST /api/voice/transcribe` — drives `STTAdapter`.
* `GET /api/voice/preferences` — returns Kokoro/Deepgram provider ids as defaults (`DEFAULT_TTS_PROVIDER="kokoro"`, `DEFAULT_STT_PROVIDER="deepgram"`).
* `PUT /api/voice/preferences` — validates ids against `TTSAdapter`/`STTAdapter`.

Retirement options (decide with Codex/Sonnet): remove the whole router, or
keep the two `preferences` routes but re-validate against the new engine's
fixed configuration. The new pipeline has no per-user provider choice
(Faster-Whisper + Qwen3-TTS are the stack), so `tts_provider_id`,
`tts_model_id`, `stt_provider_id`, `stt_model_id` preference fields become
meaningless; `voice_speed`/`voice_pitch`/`voice_enabled` should be mapped
onto the new engine's parameters or dropped deliberately.

## Frontend callers (react_ui — Gemini owns; retirement is a contract for that workstream)

* `react_ui/src/api/client.ts` — `synthesizeSpeech` → `POST /api/voice/synthesize`,
  `transcribeAudio` → `POST /api/voice/transcribe`,
  `getVoicePreferences`/`updateVoicePreferences` → `/api/voice/preferences`.
* `react_ui/src/components/VoiceControls.tsx`, `CallMode.tsx`,
  `ChatInput.tsx`, `SceneControls.tsx`, `AppShell.tsx` — consume those
  client functions / provider selectors.
* All of these must move to the new avatar WS/media flow and the old
  fetches removed; keeping them leaves a second active voice pipeline,
  which is exactly what retirement must prevent.

## Config settings to retire (`chatbot/config.py`)

`tts_provider`, `tts_model`, `tts_voice`, `tts_speed`, `tts_api_key`,
`tts_base_url`, `stt_provider`, `stt_model`, `stt_language`,
`stt_api_key`, `stt_base_url` — all Deepgram/ElevenLabs/Kokoro-era. Note
`chatbot/api/voice.py` also falls back to `settings.openrouter_api_key`
for both adapters.

## Database fields to retire

Table `voice_preferences` (`chatbot/db/models.py::VoicePreferences`,
mounted via migrations): `tts_provider_id`, `tts_model_id`,
`stt_provider_id`, `stt_model_id`, `voice_speed`, `voice_pitch`,
`voice_enabled`. Retire the table (or reduce it to whatever the new
pipeline actually persists) with a migration. Any rows referencing kokoro/
deepgram model ids become dead data.

## Tests to retire/replace

* `tests/test_voice.py`, `tests/test_voice_integration.py` — assert the
  Deepgram/Kokoro/ElevenLabs adapters, cadence mapping, and the
  `/api/voice/*` routes. Retire with the code; replace with tests for the
  new STT/TTS path owned by the media-engine workstream.

## Dependencies that can be retired

None of `chatbot/voice/` imports a heavy SDK — Deepgram/Kokoro/ElevenLabs
are plain `httpx` calls — so no Python package is freed by this retirement.
`redis`/`pgvector` etc. are unrelated. The only freed artifacts are the
API keys in the deployment environment (`STT_API_KEY`, `TTS_API_KEY`,
`DEEPGRAM`/ElevenLabs entries) and the provider config defaults.

## Ordering constraints (do not break startup while the new pipeline lands)

1. Land the new Faster-Whisper + Qwen3-TTS pipeline behind the avatar
   subsystem first; verify it serves the browser end-to-end.
2. Switch `react_ui` callers to the new flow (Gemini).
3. Remove `chatbot/api/voice.py` routes **and** the `voice_router` include
   in `chatbot/api/app.py` in the same commit as the frontend switch —
   otherwise the UI calls 404s.
4. Drop `chatbot/voice/` (`stt.py`, `tts.py`, `cadence.py`,
   `prompt_adapter.py`, `base.py`), the config settings, the
   `VoicePreferences` model + migration, and the two legacy test files.
5. Re-run startup smoke (`chatbot.api.app:create_app` import + lifespan)
   after removal; nothing else imports `chatbot.voice` (verified by grep
   at time of writing: only `chatbot/api/voice.py` imports it).

## Explicit non-goals for this workstream

* Files owned by other agents were not edited; `chatbot/api/app.py`
  include of `voice_router` stays until step 3.
* The new pipeline is NOT implemented here — this document only fixes the
  retirement contract so the old Kokoro/Deepgram/ElevenLabs path cannot
  remain an active alternative.