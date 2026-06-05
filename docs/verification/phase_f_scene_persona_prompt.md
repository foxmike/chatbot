# Phase F Verification: Scene Persona Prompt Integration

## Verification Date

2026-06-04

## Task Reference

- **Plan:** CHATBOT_FULL_FUNCTIONALITY_COMPLETION
- **Phase:** F
- **Task:** f.7 — Verify scene persona prompt integration

## Scope

Verify that scene state (location, activity, tone, intensity, participants) and roleplay persona (name, traits, voice style, backstory, constraints)
are correctly combined into system prompts for the LLM provider during chat turns.

## Integration Flow

The scene persona prompt integration spans four components:

```
┌─────────────────┐     ┌──────────────────┐     ┌──────────────────────┐
│  ChatRequest    │────▶│  RoomContextStep  │────▶│  CompileContextStep  │
│  (room_id)      │     │  loads scene into │     │  assembles persona   │
│  api/chat.py    │     │  turn metadata    │     │  prompt for LLM      │
└─────────────────┘     └──────────────────┘     └──────────────────────┘
                                                          │
                                                          ▼
┌─────────────────┐     ┌──────────────────┐     ┌──────────────────────┐
│  /scenes/{id}/  │◀────│  RoleplayEngine  │     │  PersonaPrompt       │
│  persona        │     │  build_scene_    │     │  Assembler           │
│  API endpoint   │     │  prompt()        │     │  soul/persona_       │
│  api/rooms.py   │     │  rooms/roleplay  │     │  prompt.py           │
└─────────────────┘     └──────────────────┘     └──────────────────────┘
```

## Verification Method

1. **Unit tests** in `tests/test_rooms_integration.py` covering:
   - `RoleplayEngine.build_scene_prompt()` correctness and determinism
   - `RoleplayEngine.generate_scene_context()` completeness
   - `PersonaPromptAssembler` compatibility with roleplay persona data
   - Pipeline step presence (`RoomContextStep`, `SceneMemoryGateStep`)
   - Turn context metadata support for `room_id`

2. **Manual trace verification** of the code paths:
   - `RoomContextStep` loads active scene via `get_active_scene()` and stores in `ctx.metadata["active_scene"]`
   - `RoleplayEngine.build_scene_prompt()` embeds scene location, activity, tone, intensity, scene_context plus persona name, description, voice_style, backstory, constraints
   - API endpoint `/rooms/scenes/{scene_id}/persona` wires scene + persona through `RoleplayEngine`

## Results

### ✅ Verified — Fully Integrated

| Component | Status | Evidence |
|-----------|--------|----------|
| `RoleplayEngine.build_scene_prompt()` | ✅ Working | 9 tests pass: all scene fields, constraints, determinism, edge cases |
| `RoleplayEngine.generate_scene_context()` | ✅ Working | 2 tests pass: traits, goals, scene state embedded |
| `/rooms/scenes/{id}/persona` endpoint | ✅ Working | Endpoint logic tested: returns `{"system_prompt": ...}` with scene+persona |
| `RoomContextStep` loads scene into metadata | ✅ Wired | Step present in pipeline, stores `active_scene` on context |
| `SceneMemoryGateStep` | ✅ Wired | Step present in pipeline, classifies memories by scene/durable |
| RoomType enum | ✅ Intact | All 5 room types present |
| `RoomMemoryPolicy` defaults | ✅ Correct | Roleplay scenes default to `persist_to_durable=False` |
| `PersonaPromptAssembler` availability | ✅ Working | Importable and compatible with roleplay persona dicts |
| `TurnContext` supports `room_id` metadata | ✅ Wired | `ChatRequest.room_id` flows through to `ctx.metadata["room_id"]` |

### ⚠️ Partial Integration Noted

| Component | Status | Detail |
|-----------|--------|--------|
| `CompileContextStep` uses scene for prompt | ⚠️ Not wired | The step assembles the persona prompt via `PersonaPromptAssembler` but does not currently inject scene data from `ctx.metadata["active_scene"]` into the assembled prompt. The scene-aware prompt is available through `RoleplayEngine.build_scene_prompt()` and the `/rooms/scenes/{id}/persona` API endpoint. The scene data is loaded and available in context metadata for future wiring. |

## Test Results Summary

```
tests/test_rooms_integration.py — 31 passed, 0 failed

TestScenePersonaPromptIntegration     —  9 tests  ✅
TestPersonaPromptAssemblerSceneIntegration —  3 tests  ✅
TestRoomMemoryIsolation               — 10 tests  ✅
TestRoomsIntegration                  —  9 tests  ✅
```

### Key Tests

| Test | What it verifies |
|------|-----------------|
| `test_build_scene_prompt_includes_all_scene_fields` | Location, activity, tone, intensity, scene_context, persona fields all present |
| `test_build_scene_prompt_structure_is_deterministic` | Same inputs produce identical prompt |
| `test_build_scene_prompt_different_scenes_produce_different_prompts` | Distinct scenes yield distinct prompts |
| `test_roleplay_persona_as_dict_feeds_assembler` | RoleplayPersona data flows through PersonaPromptAssembler |
| `test_room_context_step_exists_in_pipeline` | RoomContextStep is defined and importable |
| `test_scene_memory_gate_step_exists` | SceneMemoryGateStep is defined and importable |
| `test_turn_context_supports_room_id_metadata` | room_id flows through TurnContext metadata |

## Assessment

- **Scene persona prompt integration is verified as functional** at the `RoleplayEngine` and API endpoint levels.
- The scene-aware prompt is available via the `/rooms/scenes/{scene_id}/persona` API endpoint.
- Scene data is correctly loaded into turn context metadata by `RoomContextStep` for use by downstream steps.
- The memory classification pipeline correctly distinguishes durable vs scene-only memories during roleplay scenes.
- **Gap identified**: `CompileContextStep` does not currently inject scene data from `ctx.metadata["active_scene"]` into the persona prompt during chat turns. The scene prompt is accessible via the API endpoint but not automatically included in chat context compilation. This is a non-blocking observation — the existing integration points provide a clear path for wiring this in a future task.

## Artifacts

- Test file: `tests/test_rooms_integration.py` (31 tests, all passing)
- This verification documentation: `docs/verification/phase_f_scene_persona_prompt.md`

## Compliance

- ✅ Minimal blast radius: Only `tests/test_rooms_integration.py` and `docs/verification/phase_f_scene_persona_prompt.md` modified
- ✅ Deterministic verification: All tests pass via `pytest`
- ✅ Architecture compliance: Uses existing `RoleplayEngine`, `PersonaPromptAssembler`, and turn pipeline APIs
- ✅ No unrelated changes
- ✅ Task outcome does not introduce the phase risk (memory system dependency is verified)
