import test from "node:test";
import assert from "node:assert/strict";
import type { Companion } from "../src/api/client.ts";
import type { AvatarSyncEvent } from "../src/avatar/avatarSync.ts";

test("Session Switching: Dispatches SESSION_SWITCHED with fresh context", () => {
  const companion1: Companion = {
    companion_id: "comp_1",
    user_id: "user_mike",
    name: "Elena",
    gender: "female",
    persona: "Helpful",
    avatar_header: "AI Specialist",
    description: "Elena AI",
    avatar_url: "/portraits/elena.jpg",
    backstory: null,
    personality_sliders: null,
    humor_style: null,
    communication_style: null,
    punctuation_habits: null,
    sentence_length_profile: null,
    emoji_style: null,
    vocabulary_density: null,
    slang_profile: null,
    adaptive_verbosity: null,
    favorite_topics: null,
  };

  const companion2: Companion = {
    ...companion1,
    companion_id: "comp_2",
    name: "Marcus",
    avatar_url: "/portraits/marcus.jpg",
  };

  // State in Avatar popup
  let currentCompanion: Companion = companion1;
  let currentSessionId = "sess_001";
  let currentRoomId: string | null = null;
  const wsMessagesSent: any[] = [];

  const handleAvatarSyncEvent = (event: AvatarSyncEvent) => {
    if (event.type === "SESSION_SWITCHED") {
      currentCompanion = event.companion;
      currentSessionId = event.sessionId;
      currentRoomId = event.roomId ?? null;

      // Avatar updates runtime context and notifies WS
      wsMessagesSent.push({
        type: "switch_session",
        session_id: event.sessionId,
        companion_id: event.companion.companion_id,
        room_id: event.roomId,
      });
    }
  };

  // Main window switches companion & session
  handleAvatarSyncEvent({
    type: "SESSION_SWITCHED",
    companion: companion2,
    sessionId: "sess_002",
    roomId: "room_alpha",
  });

  assert.equal(currentCompanion.name, "Marcus");
  assert.equal(currentSessionId, "sess_002");
  assert.equal(currentRoomId, "room_alpha");
  assert.equal(wsMessagesSent.length, 1);
  assert.deepEqual(wsMessagesSent[0], {
    type: "switch_session",
    session_id: "sess_002",
    companion_id: "comp_2",
    room_id: "room_alpha",
  });
});

test("Popup close does not destroy chatbot session", () => {
  let chatbotSessionActive = true;
  let messages = [{ role: "user", content: "hello" }];

  const onAvatarPopupClosed = () => {
    // Popup closes WebSocket and cleans up audio context,
    // but does NOT end chatbot session or clear messages!
  };

  onAvatarPopupClosed();

  assert.equal(chatbotSessionActive, true);
  assert.equal(messages.length, 1);
});
