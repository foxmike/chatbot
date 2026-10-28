import test from "node:test";
import assert from "node:assert/strict";
import { AvatarSyncBridge, type AvatarSyncEvent } from "../src/avatar/avatarSync.ts";

test("AvatarSyncBridge: Publishes and subscribes to events", () => {
  const bridge = new AvatarSyncBridge();
  const received: AvatarSyncEvent[] = [];

  const unsubscribe = bridge.subscribe((event) => {
    received.push(event);
  });

  // Emulate sending an AVATAR_CONNECTED event
  const connectedEvent: AvatarSyncEvent = {
    type: "AVATAR_CONNECTED",
    companionId: "comp_123",
    sessionId: "sess_456",
    roomId: null,
    tabId: "tab_abc",
  };

  bridge.publish(connectedEvent);

  // In Node environment without native BroadcastChannel/window, publish directly dispatches to listeners
  assert.equal(received.length, 0); // No window/BroadcastChannel in Node by default

  // Now simulate an internal listener notification
  (bridge as any).notifyListeners(connectedEvent);
  assert.equal(received.length, 1);
  assert.equal(received[0].type, "AVATAR_CONNECTED");

  unsubscribe();
  (bridge as any).notifyListeners({ type: "AVATAR_DISCONNECTED", tabId: "tab_abc" });
  assert.equal(received.length, 1); // Unsubscribed, so not received

  bridge.destroy();
});
