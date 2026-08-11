# e

## Goal

Activate timeline, narrative threads, rituals, proactive check-ins, notifications, and initiative controls.

This phase MUST produce exactly 11 tasks as specified below:

Task e.1: Reconcile continuity table/model name mismatches. Allowed write paths: chatbot/db/models.py, docs/verification/phase_e_continuity_schema_reconciliation.md. selected_backend: codex_cli

Task e.2: Create reconciliation migration if needed. Allowed write paths: migrations/versions/0010_continuity_reconciliation.py. selected_backend: codex_cli

Task e.3: Wire timeline update step into runtime. Allowed write paths: chatbot/core/turn.py, chatbot/continuity/timeline.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task e.4: Convert agency API to durable DB-backed state. Allowed write paths: chatbot/api/agency.py, chatbot/agency/notifications.py, chatbot/agency/scheduler.py, chatbot/db/models.py. selected_backend: codex_cli

Task e.5: Add scheduler runtime loop. Allowed write paths: chatbot/agency/scheduler.py, chatbot/api/app.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task e.6: Mount TimelinePanel in app shell. Allowed write paths: react_ui/src/App.tsx, react_ui/src/components/TimelinePanel.tsx. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task e.7: Mount NotificationFeed in app shell. Allowed write paths: react_ui/src/App.tsx, react_ui/src/components/NotificationFeed.tsx. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task e.8: Mount InitiativeSettings in app shell. Allowed write paths: react_ui/src/App.tsx, react_ui/src/components/InitiativeSettings.tsx. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task e.9: Verify timeline survives restart. Allowed write paths: tests/test_continuity_integration.py, docs/verification/phase_e_timeline_restart.md. selected_backend: pi_cli, model: openrouter/qwen/qwen3-235b-a22b-thinking-2507

Task e.10: Verify notifications are user-scoped. Allowed write paths: chatbot/api/agency.py, chatbot/agency/notifications.py, docs/verification/phase_e_notification_user_scope.md. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task e.11: Write continuity/agency integration tests. Allowed write paths: tests/test_continuity_integration.py. selected_backend: pi_cli, model: openrouter/qwen/qwen3-235b-a22b-thinking-2507

## Notes

- Async: e.1 must complete before e.2 and e.3. e.4 and e.5 are independent of e.1-e.3. e.6-e.8 run after backend work. e.9-e.11 depend on all prior.
- Risk: Table/model name mismatch may require careful migration.
