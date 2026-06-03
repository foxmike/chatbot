# c

## Goal

Add emotional coherence. A dynamic bot mood/affect model (current mood, energy, closeness/trust, recent emotional context) that is distinct from fixed personality and shapes wording, length, and initiative. A user-state inference module that estimates likely user state (stressed, playful, lonely, excited, frustrated, tired, focused, grieving, angry) slowly and without overstating certainty. Conversation modes (normal, deep talk, playful, roleplay, planning, venting, bedtime/low-energy) that reshape persona prompt assembly. Mood and user-state persist to the schema for continuity and feed the existing soul and context-compiler layers.

## Notes

- Async: Wave 1 runs four parallel tasks on disjoint files: mood.py (bot affect model), user_state.py (user inference), modes.py (conversation modes), and db/models.py (add mood_state and user_state persistence fields/tables). Wave 2 has two parallel tasks: the 0002 migration (depends on db/models.py) and persona_prompt.py extension to consume mood and mode (depends on mood.py and modes.py). Wave 3 is a single task owning chatbot/core/turn.py that adds the affect-update and user-state-inference steps and threads mode through the pipeline; it depends on mood.py, user_state.py, modes.py, and the persona_prompt.py task. The test is the final fan-in.
- Risk: persona_prompt.py and turn.py are extended again here after phase J finalized them; each is owned by exactly one task in this phase and phase J must be fully accepted first so the seams are stable. The 0002 migration must be reversible and its embedding/column choices must not conflict with the 0001 core schema. Mood and user-state inference must update slowly and avoid overconfident assertions that would pollute durable memory; respect the operator answer on persistence horizon and on whether inferred user state becomes a permanent fact.
