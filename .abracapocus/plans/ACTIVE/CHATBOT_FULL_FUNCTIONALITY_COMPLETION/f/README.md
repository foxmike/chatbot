# f

## Goal

Make room/scene controls and room-scoped memory usable from the UI.

This phase MUST produce exactly 8 tasks as specified below:

Task f.1: Pass room_id through ChatRequest. Allowed write paths: chatbot/api/chat.py, react_ui/src/api/client.ts. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task f.2: Add active room state tracking in React. Allowed write paths: react_ui/src/App.tsx. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task f.3: Mount RoomSwitcher in app shell. Allowed write paths: react_ui/src/App.tsx, react_ui/src/components/RoomSwitcher.tsx. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task f.4: Mount SceneControls in app shell. Allowed write paths: react_ui/src/App.tsx, react_ui/src/components/SceneControls.tsx. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task f.5: Ensure room pipeline steps run when room_id present. Allowed write paths: chatbot/core/turn.py, chatbot/rooms/roleplay.py. selected_backend: codex_cli

Task f.6: Verify room-scoped memory isolation. Allowed write paths: tests/test_rooms_integration.py, docs/verification/phase_f_room_memory_isolation.md. selected_backend: pi_cli, model: openrouter/qwen/qwen3-235b-a22b-thinking-2507

Task f.7: Verify scene persona prompt integration. Allowed write paths: tests/test_rooms_integration.py, docs/verification/phase_f_scene_persona_prompt.md. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task f.8: Write rooms integration tests. Allowed write paths: tests/test_rooms_integration.py. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

## Notes

- Async: f.1 and f.2 in parallel. f.3 and f.4 in parallel after f.2. f.5 depends on f.1. f.6-f.8 depend on f.5.
- Risk: Room-scoped retrieval needs memory system active from Phase 3.
