import test from "node:test";
import assert from "node:assert/strict";

test("Streaming Preference: Avatar active locks streaming to true and restores previous preference on close", () => {
  // Simulate the state machine in App.tsx
  let useStreamingChat = false; // User's preference was false initially
  let isAvatarActive = false;
  let prevStreamingPref = false;

  const onAvatarConnected = () => {
    if (!isAvatarActive) {
      prevStreamingPref = useStreamingChat;
      useStreamingChat = true;
    }
    isAvatarActive = true;
  };

  const onAvatarClosedOrDisconnected = () => {
    if (isAvatarActive) {
      useStreamingChat = prevStreamingPref;
    }
    isAvatarActive = false;
  };

  // 1. Initial state
  assert.equal(useStreamingChat, false);
  assert.equal(isAvatarActive, false);

  // 2. Avatar window connects
  onAvatarConnected();
  assert.equal(isAvatarActive, true);
  assert.equal(useStreamingChat, true); // Automatically enabled and locked

  // 3. Avatar window closes
  onAvatarClosedOrDisconnected();
  assert.equal(isAvatarActive, false);
  assert.equal(useStreamingChat, false); // Restored to user's previous preference
});

test("Streaming Preference: Preserves user preference if it was already true", () => {
  let useStreamingChat = true; // User's preference was true initially
  let isAvatarActive = false;
  let prevStreamingPref = true;

  const onAvatarConnected = () => {
    if (!isAvatarActive) {
      prevStreamingPref = useStreamingChat;
      useStreamingChat = true;
    }
    isAvatarActive = true;
  };

  const onAvatarClosedOrDisconnected = () => {
    if (isAvatarActive) {
      useStreamingChat = prevStreamingPref;
    }
    isAvatarActive = false;
  };

  onAvatarConnected();
  assert.equal(useStreamingChat, true);

  onAvatarClosedOrDisconnected();
  assert.equal(useStreamingChat, true);
});

test("Takeover: Close code 4001 triggers displacement and restores streaming without loop", () => {
  let isAvatarActive = true;
  let useStreamingChat = true;
  let prevStreamingPref = false;
  let displaced = false;
  let reconnectCount = 0;

  const handleClose = (code: number) => {
    if (code === 4001) {
      displaced = true;
      if (isAvatarActive) {
        useStreamingChat = prevStreamingPref;
      }
      isAvatarActive = false;
      return; // Do NOT reconnect
    }
    reconnectCount++;
  };

  handleClose(4001);

  assert.equal(displaced, true);
  assert.equal(isAvatarActive, false);
  assert.equal(useStreamingChat, false); // Preference restored
  assert.equal(reconnectCount, 0);       // No reconnection loop
});
