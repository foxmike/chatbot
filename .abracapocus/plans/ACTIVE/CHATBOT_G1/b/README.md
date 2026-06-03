# b

## Goal

Complete the turn pipeline so the engine is a coherent companion rather than a stateless passthrough. Inject the existing soul/persona layer into prompt assembly so configured personality drives every response; load and bound working-memory conversation history so prior turns reach the provider call; and implement real memory extraction to replace the current no-op extract step so user facts and episodes persist to the existing schema. Reuses the provider router, context compiler, episodic store, and persona machinery already built in phases b, d, and e.

## Notes

- Async: Wave 1 runs three parallel tasks on disjoint files: persona_prompt.py (finalize deterministic persona assembly consumable by the pipeline), extraction.py (new module implementing fact/episode extraction from a turn), and working_memory.py (load and bound prior-turn history for context). Wave 2 is a single task owning chatbot/core/turn.py that rewrites the CallProvider/Retrieve/ExtractMemory step bodies to consume persona, history, and extraction; it depends on all three wave-1 tasks. Wave 3 is chatbot/api/chat.py wiring (pass session history and persona context into TurnContext.metadata), depending on the turn.py task. The integration test is the final fan-in task depending on the chat.py task.
- Risk: turn.py and chat.py already exist and are live; tasks must complete/wire them, not recreate them, and must preserve the existing step interface and StepResult contract. turn.py is owned by exactly one task to avoid write collisions. Persona injection must stay deterministic so behavior is reproducible across models. Memory extraction writes to the same tables retrieval reads; keep writes transactional.
