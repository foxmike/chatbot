# d

## Goal

Make personality/identity/relationship/character configuration visible and usable from the UI.

This phase MUST produce exactly 7 tasks as specified below:

Task d.1: Secure config endpoints with session ownership — verify session belongs to current user in chatbot/api/config.py. Allowed write paths: chatbot/api/config.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task d.2: Mount RelationshipSettings in settings panel. Allowed write paths: react_ui/src/App.tsx, react_ui/src/components/SettingsPanel.tsx, react_ui/src/components/RelationshipSettings.tsx. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task d.3: Mount CharacterCreator in settings panel. Allowed write paths: react_ui/src/App.tsx, react_ui/src/components/SettingsPanel.tsx, react_ui/src/components/CharacterCreator.tsx. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task d.4: Expose identity state and evolution controls — add identity UI. Allowed write paths: react_ui/src/App.tsx, react_ui/src/components/SettingsPanel.tsx, react_ui/src/components/IdentityControls.tsx, react_ui/src/api/client.ts. selected_backend: codex_cli

Task d.5: Add persona cache invalidation — invalidate PersonaCache on config updates. Allowed write paths: chatbot/api/config.py, chatbot/perf/cache.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task d.6: Verify config changes affect prompts. Allowed write paths: docs/verification/phase_d_prompt_config_effects.md, tests/test_config_integration.py. selected_backend: pi_cli, model: openrouter/qwen/qwen3-235b-a22b-thinking-2507

Task d.7: Write config integration tests. Allowed write paths: tests/test_config_integration.py. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

## Notes

- Async: d.1 is independent. d.2 and d.3 can run in parallel. d.4 depends on d.1. d.5 is independent. d.6 depends on d.1-d.5. d.7 depends on d.6.
- Risk: Stale persona cache makes UI changes appear ignored.
