# j

## Goal

End-to-end verification of all activated features, multi-user isolation, persistence/restart behavior, and documentation updates.

This phase MUST produce exactly 9 tasks as specified below:

Task j.1: Multi-user E2E isolation tests. Allowed write paths: tests/integration/test_multi_user_isolation.py. selected_backend: codex_cli

Task j.2: Restart persistence tests. Allowed write paths: tests/integration/test_restart_persistence.py. selected_backend: pi_cli, model: openrouter/deepseek/deepseek-v4-pro

Task j.3: Route inventory verification. Allowed write paths: tests/integration/test_route_inventory.py, docs/verification/phase_j_route_inventory.md. selected_backend: antigravity_cli

Task j.4: Migration audit — verify clean alembic upgrade head. Allowed write paths: docs/verification/phase_j_migration_audit.md. selected_backend: antigravity_cli

Task j.5: Update ARCHITECTURE.md. Allowed write paths: docs/ARCHITECTURE.md. selected_backend: antigravity_cli

Task j.6: Update OPERATIONS.md. Allowed write paths: docs/OPERATIONS.md. selected_backend: antigravity_cli

Task j.7: Create README.md at repo root. Allowed write paths: README.md. selected_backend: antigravity_cli

Task j.8: Verify provider graceful degradation. Allowed write paths: tests/integration/test_provider_degradation.py, docs/verification/phase_j_provider_degradation.md. selected_backend: pi_cli, model: openrouter/moonshotai/kimi-k2-0905

Task j.9: Final E2E smoke test. Allowed write paths: tests/integration/test_full_smoke.py, docs/verification/phase_j_final_smoke.md. selected_backend: pi_cli, model: openrouter/qwen/qwen3-235b-a22b-thinking-2507

## Notes

- Async: j.1-j.4 in parallel. j.5-j.7 in parallel. j.8 independent. j.9 depends on all.
- Risk: Integration tests may reveal issues requiring fixes in earlier phases.
