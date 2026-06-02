# f

## Goal

Background consolidation workers. Hierarchical summarization, contradiction resolution, and decay/pruning of stale memory, wired into an idempotent run-scoped worker. This is the autonomous-memory-evolution layer the off-the-shelf engines do not do well.

## Notes

- Async: summarizer.py, contradiction.py, and decay.py run in parallel (wave 1, disjoint). worker.py is the fan-in that schedules them (wave 2). tests fan in last.
- Risk: Workers must be idempotent and safe to re-run. Consolidation writes to the same memory tables phase d reads — keep operations transactional to avoid corrupting retrieval.
