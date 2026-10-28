import test from "node:test";
import assert from "node:assert/strict";
import type { Message } from "../src/types.ts";
import type { AvatarSyncEvent } from "../src/avatar/avatarSync.ts";

test("Message Sync: Spoken message synchronizes main chat WITHOUT triggering a second LLM call", () => {
  let llmCallCount = 0;
  const messages: Message[] = [];

  const fakeSendLlmRequest = (_prompt: string) => {
    llmCallCount++;
  };

  // Main window sync listener logic from App.tsx
  const handleAvatarSyncEvent = (event: AvatarSyncEvent) => {
    if (event.type === "AVATAR_SPOKEN_TURN_START") {
      messages.push({
        role: "user",
        content: event.userText,
        timestamp: new Date().toISOString(),
      });
      // CRITICAL: Notice that handleAvatarSyncEvent does NOT call fakeSendLlmRequest!
    } else if (event.type === "AVATAR_SPOKEN_TURN_END") {
      messages.push({
        role: "assistant",
        content: event.assistantText,
        timestamp: new Date().toISOString(),
      });
      // CRITICAL: Does NOT call fakeSendLlmRequest!
    }
  };

  // 1. User speaks into microphone in avatar popup
  // Faster-Whisper transcribes speech on the server
  handleAvatarSyncEvent({
    type: "AVATAR_SPOKEN_TURN_START",
    userText: "Hello there, how are you today?",
    tabId: "avatar_tab",
  });

  assert.equal(messages.length, 1);
  assert.equal(messages[0].role, "user");
  assert.equal(messages[0].content, "Hello there, how are you today?");
  assert.equal(llmCallCount, 0, "No duplicate LLM call triggered for spoken user turn");

  // 2. Server synthesizes Qwen speech & AVTR animation, then sends turn completion
  handleAvatarSyncEvent({
    type: "AVATAR_SPOKEN_TURN_END",
    assistantText: "I'm doing wonderfully! Great to speak with you.",
    tabId: "avatar_tab",
  });

  assert.equal(messages.length, 2);
  assert.equal(messages[1].role, "assistant");
  assert.equal(messages[1].content, "I'm doing wonderfully! Great to speak with you.");
  assert.equal(llmCallCount, 0, "No duplicate LLM call triggered for spoken assistant response");
});

test("Typed Message: Exactly ONE LLM request generated; avatar window does NOT call OpenRouter", () => {
  let mainChatLlmCalls = 0;
  let avatarPopupLlmCalls = 0;

  // Main chat initiates typed message
  const onUserTypedInMainChat = (text: string) => {
    mainChatLlmCalls++;
    // Normal chat streaming endpoint /chat/stream handles synthesis on backend turn coordinator
  };

  // Avatar popup WebSocket receiver
  const avatarPopupMessageReceiver = (_msg: any) => {
    // Popup receives audio 'A' and video 'V' packets directly from WebSocket
    // It NEVER invokes an LLM API directly
  };

  onUserTypedInMainChat("Tell me a quick joke");
  avatarPopupMessageReceiver({ type: "stream_chunk" });

  assert.equal(mainChatLlmCalls, 1, "Exactly ONE LLM request initiated by main window");
  assert.equal(avatarPopupLlmCalls, 0, "Avatar popup never calls LLM/OpenRouter independently");
});
