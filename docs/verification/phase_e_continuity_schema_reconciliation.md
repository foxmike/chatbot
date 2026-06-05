# Phase E Continuity Schema Reconciliation

## Task

- Task: e.1 Reconcile continuity table/model name mismatches.
- Allowed write paths:
  - `chatbot/db/models.py`
  - `docs/verification/phase_e_continuity_schema_reconciliation.md`

## Findings

- `migrations/versions/0005_continuity.py` creates the canonical continuity tables:
  - `timeline_entries`
  - `narrative_threads`
  - `rituals`
- `chatbot/db/models.py` already maps the canonical ORM classes to those table names:
  - `TimelineEntry.__tablename__ = "timeline_entries"`
  - `NarrativeThread.__tablename__ = "narrative_threads"`
  - `Ritual.__tablename__ = "rituals"`
- The continuity package still contains legacy module-local model names for the same concepts:
  - `RelationshipTimelineEntryModel`
  - `CompanionRitualModel`

## Reconciliation

- Kept the central `chatbot.db.models` table mappings aligned with the migration-backed schema.
- Added compatibility model-name aliases in `chatbot/db/models.py`:
  - `RelationshipTimelineEntryModel = TimelineEntry`
  - `CompanionRitualModel = Ritual`
- No new continuity tables or migrations were introduced.

## Phase Risk Assessment

- The phase risk was schema drift from stale continuity table/model names.
- This task keeps `chatbot.db.models` aligned to the existing migration table names and avoids creating or renaming tables outside the active schema.
- The legacy module-local continuity models remain outside this task's write scope; this reconciliation does not introduce additional schema drift.

## Verification

- Passed: `python -m py_compile chatbot/db/models.py`
