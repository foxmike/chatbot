# g

## Goal

Chat orchestration spine and API. A thin explicit turn pipeline (receive -> retrieve -> compile context -> call provider -> extract/persist memory -> respond) with a clean step interface, plus the /chat, /sessions, and streaming endpoints. No LangGraph; the pipeline is first-party and fully inspectable.

## Notes

- Async: steps.py (step interface) + turn.py (pipeline) land first (wave 1). chat.py, sessions.py, and streaming.py run in parallel in wave 2 on disjoint files. test_chat_e2e.py is the wave-3 fan-in and the real end-to-end gate.
- Risk: This is the spine that fans in providers (b), memory (d), and soul (e). The risk is leaking framework abstractions in; keep the step interface minimal so branching/tools can be added later behind it.
