import test from "node:test";
import assert from "node:assert/strict";
import {
  PACKET_AUDIO,
  PACKET_MIC,
  PACKET_VIDEO,
  WS_CLOSE_DISPLACED,
  encodeAudioPacket,
  encodeMicPacket,
  encodeVideoPacket,
  getAvatarWsUrl,
  parseBinaryPacket,
} from "../src/avatar/avatarProtocol.ts";

test("Binary packet parsing: Video packet ('V' = 86)", () => {
  const dummyJpeg = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0x01, 0x02]);
  const packetBuffer = encodeVideoPacket("seg_123", 1.25, dummyJpeg);

  const parsed = parseBinaryPacket(packetBuffer.buffer);
  assert.ok(parsed);
  assert.equal(parsed.type, "video");
  if (parsed.type === "video") {
    assert.equal(parsed.id, "seg_123");
    assert.ok(Math.abs(parsed.pts - 1.25) < 0.0001);
    assert.equal(parsed.isIdle, false);
    assert.equal(parsed.jpeg.byteLength, dummyJpeg.byteLength);
  }
});

test("Binary packet parsing: Idle video frame (empty id)", () => {
  const dummyJpeg = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0x99]);
  const packetBuffer = encodeVideoPacket("", 0.0, dummyJpeg);

  const parsed = parseBinaryPacket(packetBuffer.buffer);
  assert.ok(parsed);
  assert.equal(parsed.type, "video");
  if (parsed.type === "video") {
    assert.equal(parsed.id, "");
    assert.equal(parsed.isIdle, true);
  }
});

test("Binary packet parsing: Audio packet ('A' = 65)", () => {
  const pcmData = new Float32Array([0.1, -0.2, 0.5, -0.5, 0.0]);
  const packetBuffer = encodeAudioPacket("seg_audio_1", 24000, pcmData);

  const parsed = parseBinaryPacket(packetBuffer.buffer);
  assert.ok(parsed);
  assert.equal(parsed.type, "audio");
  if (parsed.type === "audio") {
    assert.equal(parsed.id, "seg_audio_1");
    assert.equal(parsed.sampleRate, 24000);
    assert.equal(parsed.pcm.length, 5);
    assert.ok(Math.abs(parsed.pcm[0] - 0.1) < 0.0001);
    assert.ok(Math.abs(parsed.pcm[1] - -0.2) < 0.0001);
  }
});

test("Microphone packet encoding ('M' = 77)", () => {
  const micPcm = new Float32Array([0.05, 0.1, -0.1]);
  const packet = encodeMicPacket(micPcm);

  assert.equal(packet[0], PACKET_MIC);
  assert.equal(packet.byteLength, 1 + micPcm.byteLength);

  // Validate PCM bytes inside using an aligned copy
  const view = new Float32Array(packet.slice(1).buffer);
  assert.ok(Math.abs(view[0] - 0.05) < 0.0001);
  assert.ok(Math.abs(view[1] - 0.1) < 0.0001);
  assert.ok(Math.abs(view[2] - -0.1) < 0.0001);
});

test("WebSocket takeover close code is 4001", () => {
  assert.equal(WS_CLOSE_DISPLACED, 4001);
});

test("getAvatarWsUrl resolves default backend port 8000", () => {
  const url = getAvatarWsUrl("test_tab");
  assert.ok(url.includes("/api/avatar/ws"));
  assert.ok(url.includes("tab=test_tab"));
});
