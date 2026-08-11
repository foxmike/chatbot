# g

## Goal

Make voice features operational or explicitly configurable.

This phase MUST produce exactly 9 tasks as specified below:

Task g.1: Add TTS/STT provider config to chatbot/config.py. Allowed write paths: chatbot/config.py. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task g.2: Implement TTS provider call — replace NotImplementedError in chatbot/voice/tts.py. Allowed write paths: chatbot/voice/tts.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task g.3: Implement STT provider call — replace NotImplementedError in chatbot/voice/stt.py. Allowed write paths: chatbot/voice/stt.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task g.4: Create voice_preferences migration. Allowed write paths: migrations/versions/0011_voice_preferences.py, chatbot/db/models.py. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task g.5: Secure synthesize/transcribe endpoints with auth. Allowed write paths: chatbot/api/voice.py. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task g.6: Mount VoiceControls in app shell. Allowed write paths: react_ui/src/App.tsx, react_ui/src/components/VoiceControls.tsx. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task g.7: Mount CallMode in app shell. Allowed write paths: react_ui/src/App.tsx, react_ui/src/components/CallMode.tsx. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task g.8: Verify cadence mapping affects TTS. Allowed write paths: chatbot/voice/cadence.py, chatbot/voice/tts.py, docs/verification/phase_g_cadence_tts.md. selected_backend: pi_cli, model: openrouter/qwen/qwen3-235b-a22b-thinking-2507

Task g.9: Write voice integration tests. Allowed write paths: tests/test_voice_integration.py. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

## Notes

- Async: g.1-g.5 can run in parallel. g.6 and g.7 depend on g.1 and g.5. g.8 depends on g.2. g.9 depends on all.
- Risk: Voice features should be hidden when unconfigured.
