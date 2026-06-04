# h

## Goal

Make avatar, appearance, image messages, and visual provider behavior usable.

This phase MUST produce exactly 9 tasks as specified below:

Task h.1: Create visual tables migration. Allowed write paths: migrations/versions/0012_visual_tables.py, chatbot/db/models.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task h.2: Register visual API router — create chatbot/api/visual.py, register in app.py. Allowed write paths: chatbot/api/visual.py, chatbot/api/app.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task h.3: Configure image generation provider settings. Allowed write paths: chatbot/config.py, chatbot/visual/generation.py. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task h.4: Configure image understanding provider settings. Allowed write paths: chatbot/config.py, chatbot/visual/understanding.py. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task h.5: Wire appearance/outfit continuity into companion display. Allowed write paths: chatbot/visual/appearance.py, chatbot/companions/companion.py. selected_backend: codex_cli

Task h.6: Mount AvatarDisplay component. Allowed write paths: react_ui/src/App.tsx, react_ui/src/components/AvatarDisplay.tsx. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task h.7: Mount ImageMessage component. Allowed write paths: react_ui/src/App.tsx, react_ui/src/components/ImageMessage.tsx. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task h.8: Verify graceful provider failure. Allowed write paths: chatbot/visual/generation.py, chatbot/visual/understanding.py, docs/verification/phase_h_provider_failure.md. selected_backend: antigravity_cli

Task h.9: Write visual integration tests. Allowed write paths: tests/test_visual_integration.py. selected_backend: pi_cli, model: openrouter/qwen/qwen3-235b-a22b-thinking-2507

## Notes

- Async: h.1-h.4 in parallel. h.5 depends on h.2. h.6 and h.7 depend on h.2. h.8 depends on h.2-h.4. h.9 depends on all.
- Risk: Visual features should degrade gracefully when providers are unconfigured.
