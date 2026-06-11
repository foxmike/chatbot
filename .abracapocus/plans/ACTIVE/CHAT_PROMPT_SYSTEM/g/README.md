# g

## Goal

Add prompt observability and telemetry. Create a prompt effectiveness telemetry module that logs which prompt template, model, mode, and formatter combo produced each response. Wire telemetry into existing observability trace emission. Create a prompt compression module for small-context local models that summarizes or prioritizes prompt sections to fit within constrained windows. Update ARCHITECTURE.md and OPERATIONS.md to document the new prompt system, its configuration, and operational procedures.

## Notes

- Async: Wave 1: g.1 (prompt_telemetry.py), g.3 (prompt_compressor.py) — new files, disjoint. Wave 2: g.2 (traces.py + turn.py) depends on g.1. Wave 3: g.4 (ARCHITECTURE.md) and g.5 (OPERATIONS.md) depend on all implementation tasks completing. g.4 and g.5 are disjoint and can run in parallel.
- Risk: turn.py is modified again — must be additive to phase b and f changes. Documentation tasks need extended timeout since they require reading multiple source files.
