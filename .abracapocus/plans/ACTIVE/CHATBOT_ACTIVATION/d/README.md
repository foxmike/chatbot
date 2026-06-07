# d

## Goal

Implement rooms as functional conversation containers with behavioral modes and roleplay scenes. Auto-create a linked session when a room is created. Load room session messages into the chat panel when a room is clicked. Filter messages by room in frontend and backend. Inject room-type-specific system prompt sections (one_on_one_chat: default conversational, roleplay_scene: scene-aware immersive, project: task-oriented structured, emotional_support: empathetic validating reduced-humor, memory_review: memory-surfacing). Apply room-type memory policies. Auto-create default one_on_one_chat room on companion creation. Auto-populate companion_id from active companion. Replace companion_id text input with dropdown. Convert left sidebar from sessions to rooms. Add scene management UI for roleplay rooms. Wire scene state into system prompt via RoleplayEngine. Display active scene status in roleplay chat.

## Notes

- Async: Wave 1: update chatbot/rooms/room.py (auto-create session), update chatbot/api/sessions.py (room_id support), create react_ui/src/components/ScenePanel.tsx. All disjoint. Wave 2: update chatbot/api/rooms.py (room-session linking), update react_ui/src/components/RoomSwitcher.tsx (companion dropdown). Disjoint. Wave 3: update chatbot/api/chat.py (room filtering + room-type prompt injection). Wave 4: update chatbot/core/turn.py (RoomContextStep scene injection, depends on wave 3). Wave 5: update chatbot/api/streaming.py (room filtering, serialized after chat.py). Wave 6: update react_ui/src/api/client.ts (room-session API calls). Wave 7: update react_ui/src/App.tsx (room sidebar, scene mount, wire room selection), update react_ui/src/components/ChatInput.tsx (room context). Wave 7 depends on all prior waves.
- Risk: This phase touches many shared files. chat.py, streaming.py, turn.py, App.tsx, and client.ts are all fan-in points that must be serialized. Phase ordering after A/B/C ensures prior changes are stable.
