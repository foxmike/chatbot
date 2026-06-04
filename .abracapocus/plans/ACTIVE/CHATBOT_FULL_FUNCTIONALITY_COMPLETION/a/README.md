# a

## Goal

Fix frontend/backend route mismatches, register missing routers, add auth ownership to unsafe endpoints, and build a navigable app shell that mounts existing React components.

This phase MUST produce exactly 12 tasks as specified below:

Task a.1: Fix memory router prefix — change from /api/memories to /memories in chatbot/api/memory.py. Allowed write paths: chatbot/api/memory.py. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task a.2: Fix voice router prefix — change from /api/voice to /voice in chatbot/api/voice.py. Allowed write paths: chatbot/api/voice.py. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task a.3: Register companion router — create chatbot/api/companions.py using existing CompanionManager, register in app.py. Allowed write paths: chatbot/api/companions.py, chatbot/api/app.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task a.4: Register continuity routers — create chatbot/api/continuity.py with /timeline, /narrative, /rituals routes matching frontend client paths, register in app.py. Allowed write paths: chatbot/api/continuity.py, chatbot/api/app.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task a.5: Add auth ownership to config endpoints — add get_current_user dependency to chatbot/api/config.py, verify session belongs to current user. Allowed write paths: chatbot/api/config.py. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task a.6: Add auth to memory endpoints — add get_current_user dependency to chatbot/api/memory.py, scope queries by user. Allowed write paths: chatbot/api/memory.py. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task a.7: Add auth to agency endpoints — add get_current_user dependency to chatbot/api/agency.py, remove arbitrary user_id params. Allowed write paths: chatbot/api/agency.py. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task a.8: Add auth to rooms endpoints — add get_current_user dependency to chatbot/api/rooms.py, remove arbitrary user_id params. Allowed write paths: chatbot/api/rooms.py. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task a.9: Add admin auth gate — add admin authorization check to chatbot/api/admin.py endpoints. Allowed write paths: chatbot/api/admin.py. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task a.10: Add auth to streaming endpoint — add auth dependency, session ownership check, DB history loading, and message persistence to chatbot/api/streaming.py. Allowed write paths: chatbot/api/streaming.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task a.11: Build navigable app shell — add tabbed/panel navigation to react_ui/src/App.tsx for settings, memory, timeline, rooms, voice, admin panels. Create AppShell.tsx and SettingsPanel.tsx. Allowed write paths: react_ui/src/App.tsx, react_ui/src/components/AppShell.tsx, react_ui/src/components/SettingsPanel.tsx. selected_backend: codex_cli

Task a.12: Mount CompanionSelector in auth flow — wire CompanionSelector into the authenticated app flow in App.tsx so users can create/select companions. Depends on a.3 and a.11. Allowed write paths: react_ui/src/App.tsx, react_ui/src/components/CompanionSelector.tsx. selected_backend: codex_cli

## Notes

- Async: Tasks a.1 through a.9 are independent and can run in parallel (disjoint files). a.10 is independent. a.11 depends on route fixes. a.12 depends on a.3 and a.11.
- Risk: Exposing UI for endpoints that still lack auth is a data isolation risk. Verify auth is applied before mounting any panel.
