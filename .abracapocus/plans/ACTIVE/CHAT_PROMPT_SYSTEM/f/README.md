# f

## Goal

Build mode and platform routing features. Create per-mode response contracts that define behavioral specs for distinct conversation modes (bedtime, advisor, deep_talk, roleplay, brainstorming, companion) including tone, length, pacing, and active prompt sections. Create voice-vs-text prompt variant logic that adjusts formatting for TTS output. Create temperature and sampling profile configuration per model family and per mode. Wire fast-path vs deep-path routing that uses intent classification from phase b to select lightweight or heavy prompt assembly. Wire mode contracts into room type configuration.

## Notes

- Async: Wave 1: f.1 (mode_contracts.py), f.2 (voice/prompt_adapter.py), f.3 (providers/sampling.py) — all new files, disjoint. Wave 2: f.4 (turn.py) depends on f.1 and f.3 for fast/deep routing. f.5 (rooms.py) depends on f.1 for mode contract integration. Wave 3: f.4 must complete before any further turn.py modifications.
- Risk: turn.py is the pipeline core — routing changes must preserve backward compatibility. Sampling profiles must not override explicit user temperature settings if they exist.
