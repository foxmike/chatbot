# Global Execution Rules

- Respect each task's allowed_write_paths.
- Run deterministic verification before accepting changes.
- Do not modify state/runtime_state.json, reports/, logs/, or virtual environments.
- Verification passing is the only hard acceptance gate. A task where verification passes, with no write policy violations and no scope violations, must be accepted. Do not add blocking logic, heuristics, or new rejection paths to the acceptance pipeline, supervisor, or verifier unless the task contract explicitly requires it by name.
- The reconciler and change assessor are advisory. Their output is recorded as metadata only. They must never cause a task to be rejected or blocked.
