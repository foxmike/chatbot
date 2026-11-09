import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  DEFAULT_TTS_MODE,
  TTS_MODES,
  TTS_MODE_STORAGE_KEY,
  isTtsMode,
  loadTtsMode,
  saveTtsMode,
} from "../src/avatar/ttsMode.ts";
import { buildAuthMessage, buildTtsModeMessage } from "../src/avatar/avatarProtocol.ts";

function memoryStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    data,
  };
}

const CONTEXT = { token: "t", sessionId: "s1", companionId: "c1", roomId: null, tabId: "tab" };

test("Natural is the default, both as a constant and when nothing is saved", () => {
  assert.equal(DEFAULT_TTS_MODE, "natural");
  assert.equal(loadTtsMode(memoryStorage()), "natural");
  assert.equal(loadTtsMode(null), "natural");
});

test("the dropdown offers Natural first, then Fast, with display labels", () => {
  assert.deepEqual(
    TTS_MODES.map((mode) => [mode.id, mode.label]),
    [
      ["natural", "Natural"],
      ["fast", "Fast"],
    ],
  );
  assert.equal(TTS_MODES[0].id, DEFAULT_TTS_MODE);
});

test("the mode list is extensible: ids are unique and every listed mode is accepted", () => {
  const ids = TTS_MODES.map((mode) => mode.id);
  assert.equal(new Set(ids).size, ids.length);
  for (const id of ids) assert.equal(isTtsMode(id), true);
});

test("Fast can be selected and is stored in localStorage", () => {
  const storage = memoryStorage();

  assert.equal(saveTtsMode(storage, "fast"), true);

  assert.equal(storage.data.get(TTS_MODE_STORAGE_KEY), "fast");
});

test("the saved mode is restored when the popup is reopened and across sessions", () => {
  const storage = memoryStorage();
  saveTtsMode(storage, "fast");

  assert.equal(loadTtsMode(storage), "fast"); // reopened popup
  assert.equal(loadTtsMode(memoryStorage(Object.fromEntries(storage.data))), "fast"); // new app session

  saveTtsMode(storage, "natural");
  assert.equal(loadTtsMode(storage), "natural");
});

test("invalid modes are rejected and never stored; invalid stored values fall back to Natural", () => {
  const storage = memoryStorage();
  saveTtsMode(storage, "fast");

  for (const bad of ["slow", "Natural", "", "FAST", null, undefined, 3, {}]) {
    assert.equal(isTtsMode(bad), false);
    assert.equal(saveTtsMode(storage, bad), false);
  }
  assert.equal(storage.data.get(TTS_MODE_STORAGE_KEY), "fast"); // unchanged

  assert.equal(loadTtsMode(memoryStorage({ [TTS_MODE_STORAGE_KEY]: "turbo" })), "natural");
});

test("unavailable storage never breaks the popup", () => {
  const broken = {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => {
      throw new Error("blocked");
    },
  };

  assert.equal(loadTtsMode(broken), "natural");
  assert.equal(saveTtsMode(broken, "fast"), true); // still selectable for this window
});

test("the saved mode is sent with every handshake, so a reconnect restores it", () => {
  const storage = memoryStorage();
  saveTtsMode(storage, "fast");

  const first = buildAuthMessage(CONTEXT, null, loadTtsMode(storage));
  const afterReconnect = buildAuthMessage(CONTEXT, null, loadTtsMode(storage));

  assert.equal(first.tts_mode, "fast");
  assert.equal(afterReconnect.tts_mode, "fast");
  assert.equal(first.type, "auth");
});

test("a fresh browser's handshake carries Natural", () => {
  assert.equal(buildAuthMessage(CONTEXT, null, loadTtsMode(memoryStorage())).tts_mode, "natural");
});

test("a handshake from an older caller (no mode) is unchanged", () => {
  assert.equal("tts_mode" in buildAuthMessage(CONTEXT, null), false);
});

test("changing the dropdown sends only a tts_mode message, so it applies to the next turn", () => {
  const message = buildTtsModeMessage("fast");

  assert.deepEqual(message, { type: "tts_mode", mode: "fast" });
  // It is not an interruption, skip or microphone message: an active turn is not touched.
  for (const forbidden of ["interrupt", "barge_in", "skip_speech", "mic", "interruption_ack"]) {
    assert.notEqual(message.type, forbidden);
  }
});

test("each avatar session sends its own mode and they do not affect each other", () => {
  const a = memoryStorage();
  const b = memoryStorage();
  saveTtsMode(a, "fast");

  const forA = buildAuthMessage({ ...CONTEXT, sessionId: "A" }, null, loadTtsMode(a));
  const forB = buildAuthMessage({ ...CONTEXT, sessionId: "B" }, null, loadTtsMode(b));

  assert.equal(forA.tts_mode, "fast");
  assert.equal(forB.tts_mode, "natural");
  saveTtsMode(b, "fast");
  assert.equal(loadTtsMode(a), "fast");
  assert.equal(buildAuthMessage({ ...CONTEXT, sessionId: "A" }, null, "natural").tts_mode, "natural");
});

test("the popup footer shows 'TTS:' between the microphone and Skip Speech controls", () => {
  const source = readFileSync(new URL("../src/components/AvatarPopup.tsx", import.meta.url), "utf8");
  const footer = source.slice(source.indexOf("<footer"));

  const mic = footer.indexOf("Enable microphone");
  const tts = footer.indexOf('id="tts-mode"');
  const skip = footer.indexOf('id="skip-speech"');

  assert.ok(mic > 0 && tts > mic && skip > tts, "TTS dropdown must sit between mic and skip");
  assert.match(footer, /TTS:/);
  assert.match(footer, /TTS_MODES\.map/); // options come from the list, not hard-coded
  assert.match(source, /loadTtsMode\(accountLocalStorage\(\)\)/); // restored on open, per account
  assert.match(source, /runtimeRef\.current\?\.sendTtsMode\(\)/); // applied to the next turn
});
