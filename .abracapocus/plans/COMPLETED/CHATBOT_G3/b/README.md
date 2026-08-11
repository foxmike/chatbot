# b

## Goal

Roleplay and room model. A scene state engine (location, activity, tone, intensity, pause/resume across sessions). A roleplay engine with scene-aware persona and memory isolation so scene content does not pollute durable factual memory unless marked. Typed rooms (one-on-one chat, roleplay scene, project room, emotional support room, memory review room) with per-room memory policy and room-scoped retrieval. UI for room switching and scene control.

## Notes

- Async: Wave 1 (disjoint): rooms/__init__.py + room.py as one package task, scene.py, and db/models.py room/scene tables. Wave 2: roleplay.py (depends on scene.py), the 0007 migration (depends on models.py), retrieval.py room-scoped filtering (depends on room.py), api/rooms.py endpoints (depends on room.py and scene.py), and client.ts extension. Wave 3: chatbot/core/turn.py room/scene-aware branching (depends on room.py, scene.py, roleplay.py, and the retrieval task), api/app.py registering the rooms router (depends on rooms.py), and the RoomSwitcher and SceneControls React components (depend on client.ts). Test fan-in last.
- Risk: retrieval.py and turn.py are core differentiators; each is owned by one task and the changes must be additive and room-gated so default one-on-one behavior is unchanged. db/models.py and api/app.py single-owner. The 0007 migration must be reversible with its real head revision verified at materialize time. Honor the roleplay memory-isolation default from the operator answer.
