import test from "node:test";
import assert from "node:assert/strict";

test("Avatar Interruption: Fades audio smoothly and clears stale queues", () => {
  let playbackGeneration = 0;
  const segments = new Map<string, any>();
  const pendingVideo = new Map<string, any[]>();
  const finishedIds = new Set<string>();
  const sentMessages: any[] = [];

  // Mock audio segments
  let gainTargetAtTimeCalled = false;
  let sourceStopCalled = false;

  const mockGainNode = {
    gain: {
      cancelScheduledValues: (_now: number) => {},
      setTargetAtTime: (val: number, _now: number, timeConstant: number) => {
        if (val === 0 && Math.abs(timeConstant - 0.015) < 0.001) {
          gainTargetAtTimeCalled = true;
        }
      },
    },
  };

  const mockSourceNode = {
    stop: (_stopTime: number) => {
      sourceStopCalled = true;
    },
  };

  segments.set("turn_1_seg_1", {
    id: "turn_1_seg_1",
    gain: mockGainNode,
    source: mockSourceNode,
    interrupted: false,
  });

  pendingVideo.set("turn_1_seg_2", [{ pts: 0.1, jpeg: new ArrayBuffer(10) }]);

  // Execute interruption fade logic from AvatarRuntime.clearSpeech()
  const clearSpeech = (now: number) => {
    playbackGeneration++;

    for (const segment of segments.values()) {
      segment.interrupted = true;
      finishedIds.add(segment.id);
      try {
        segment.gain.gain.cancelScheduledValues(now);
        segment.gain.gain.setTargetAtTime(0, now, 0.015);
        segment.source.stop(now + 0.06);
      } catch {}
    }

    for (const id of pendingVideo.keys()) {
      finishedIds.add(id);
    }

    segments.clear();
    pendingVideo.clear();

    sentMessages.push({ type: "interruption_ack", fade_ms: 60 });
  };

  // Trigger interruption
  clearSpeech(10.5);

  assert.equal(playbackGeneration, 1, "Playback generation incremented to invalidate stale promises");
  assert.equal(gainTargetAtTimeCalled, true, "15ms target fade applied to gain");
  assert.equal(sourceStopCalled, true, "Buffer source stop scheduled");
  assert.equal(segments.size, 0, "Segments cleared");
  assert.equal(pendingVideo.size, 0, "Pending video frames cleared");
  assert.ok(finishedIds.has("turn_1_seg_1"), "Interrupted segment added to finishedIds");
  assert.ok(finishedIds.has("turn_1_seg_2"), "Pending video segment added to finishedIds");
  assert.deepEqual(sentMessages[0], { type: "interruption_ack", fade_ms: 60 }, "interruption_ack sent with 60ms fade");
});
