# d

## Goal

Enhance companion personality in prompts. Add linguistic_fingerprint structured columns to the Companion model (punctuation_habits, sentence_length_profile, emoji_style, vocabulary_density, slang_profile). Create Alembic migration. Build a linguistic fingerprint injection module that converts fingerprint data into concrete prompt directives. Add anti-repetition directive generation that tracks recent output patterns. Add assistantism scrubber directives that suppress corporate AI tone. Add a pacing control system driven by scene type and conversation momentum. Add adaptive verbosity tracking as a user-level preference. Wire fingerprint fields into the CompanionEditor UI.

## Notes

- Async: Wave 1: d.1 (models.py), d.3 (linguistic_fingerprint.py), d.4 (anti_repetition.py), d.6 (pacing.py) — d.3, d.4, d.6 are new files. d.1 touches models.py only. Wave 2: d.2 (migration) depends on d.1. d.5 (prompt_layers.py) depends on d.3 and d.4. d.7 (persona_prompt.py) depends on d.3. Wave 3: d.8 (CompanionEditor.tsx) depends on d.1 and d.2 for schema awareness.
- Risk: models.py is a shared file — only d.1 writes it in this phase. Migration must be additive only (new nullable columns). CompanionEditor.tsx is a complex existing component.
