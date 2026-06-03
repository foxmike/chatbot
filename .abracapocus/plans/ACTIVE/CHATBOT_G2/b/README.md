# b

## Goal

Memory management and transparency. A memory control API (view, pin, delete, edit, mark private/temporary/permanent, do-not-remember, remember-forever, correct-a-misunderstanding). Explainable memory via provenance tracking (explicit vs inferred, source turn, confidence). Searchable conversation history with date/topic filtering and export. Per-memory privacy levels. UI panels for memory review and history search. Operates over the schema and extraction produced in earlier phases.

## Notes

- Async: Wave 1 (disjoint): provenance.py, controls.py, search.py, export.py, and db/models.py provenance/privacy field additions. Wave 2: the 0004 migration (depends on models.py), api/memory.py endpoints (depends on controls/search/export/provenance), and client.ts extension. Wave 3: api/app.py registering the memory router (depends on memory.py), and the React MemoryPanel and HistorySearch components in parallel (depend on client.ts). Test fan-in last.
- Risk: Memories created before this phase have no provenance; handle null provenance per the operator answer rather than crashing. db/models.py and api/app.py single-owner. The 0004 migration must be reversible and add columns without breaking the 0001 schema or pgvector column; verify the real head revision number at materialize time. Delete and privacy operations must be transactional and must not corrupt vector retrieval state.
