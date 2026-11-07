"""Engine + MediaAdapter + AvatarCoordinator together, on fake Qwen/AVTR workers."""
import asyncio
from types import SimpleNamespace

import numpy as np

from chatbot.avatar.coordinator import AvatarBinding, AvatarCoordinator
from chatbot.avatar.portraits import PortraitNotFoundError
from chatbot.avatar.runtime import AvatarRuntimeHost
from chatbot.avatar.protocol import unpack_frame

from .helpers import FakeVoices, Harness, wait_for


class FakeSocket:
    def __init__(self):
        self.json, self.binary, self.closed_with = [], [], None

    async def send_json(self, data):
        self.json.append(data)

    async def send_bytes(self, data):
        self.binary.append(data)

    async def close(self, code, reason):
        self.closed_with = (code, reason)


class FakePortraits:
    def __init__(self, error=None):
        self.error, self.calls = error, []

    async def get_portrait(self, user_id, session_id=None, companion_id=None):
        self.calls.append((user_id, session_id, companion_id))
        if self.error:
            raise self.error
        return SimpleNamespace(cache_key=f"{user_id}:{companion_id}:img1", png_bytes=b"\x89PNG\r\n\x1a\nfake")


async def rig(tmp_path, portraits=None, voices=None):
    h = Harness(auto_playback=False)
    host = AvatarRuntimeHost(engine=h.engine, portraits=portraits or FakePortraits(),
                             voices=voices or FakeVoices())
    host._portrait_dir = tmp_path
    ws = FakeSocket()
    binding = AvatarBinding("session-1", "user-1", "companion-1", ws)
    coordinator = AvatarCoordinator()
    spoken = []

    async def on_transcript(text):
        spoken.append(text)
    binding.on_transcript = on_transcript
    await coordinator.connect(binding, host.factory)
    return h, host, ws, binding, coordinator, spoken


async def ack_audio(coordinator, binding, ws, seen):
    """Play the browser role: acknowledge each audio frame once."""
    for packet in list(ws.binary):
        if packet[:1] == b"A":
            segment_id = unpack_frame(packet)[1]
            if segment_id not in seen:
                seen.add(segment_id)
                await coordinator.control(binding, {"type": "playback_end", "id": segment_id})


async def test_start_selects_the_companions_portrait_and_announces_config(tmp_path):
    h, host, ws, binding, coordinator, _ = await rig(tmp_path)
    assert host.portraits.calls == [("user-1", "session-1", "companion-1")]
    assert h.engine.portrait_id and any(m.get("type") == "portrait" or m.get("path", "").endswith(".png")
                                        for m in h.avatar.sent)
    assert {"type": "config", "sync_buffer_ms": 60} in ws.json
    assert {"type": "status", "value": "Listening"} in ws.json
    await coordinator.close()


async def test_missing_portrait_fails_start_with_a_clear_message(tmp_path):
    h = Harness()
    host = AvatarRuntimeHost(engine=h.engine, portraits=FakePortraits(PortraitNotFoundError("no portrait", code="no_portrait")))
    ws = FakeSocket()
    binding = AvatarBinding("s", "u", "c", ws)
    coordinator = AvatarCoordinator()
    try:
        await coordinator.connect(binding, host.factory)
        raise AssertionError("connect should fail")
    except RuntimeError as exc:
        assert "portrait" in str(exc)
    assert any(m.get("type") == "error" and "portrait" in m["error"] for m in ws.json)
    assert "s" not in coordinator.bindings and h.engine.sink is None


async def test_typed_turn_streams_text_through_to_segments_audio_video_and_drains(tmp_path):
    h, host, ws, binding, coordinator, _ = await rig(tmp_path)
    turn = await coordinator.begin(binding, source="spoken")
    for word in "Hello there, my friend. How was your day today?".split(" "):
        coordinator.publish(binding, turn, word + " ")
        await asyncio.sleep(0)
    coordinator.end(binding, turn)
    seen = set()
    for _ in range(300):
        await ack_audio(coordinator, binding, ws, seen)
        if turn.drained and len(turn.acknowledged) == len(turn.segments) and turn.segments:
            break
        await asyncio.sleep(0.01)
    await asyncio.wait_for(coordinator.wait_playback(turn), 3)
    assert turn.segments and turn.drained
    # Segment text is the ledger of what was spoken, in order, with separators.
    assert "".join(turn.segments.values()).strip() == "Hello there, my friend. How was your day today?"
    assert turn.heard_text.strip() == "".join(turn.segments.values()).strip()
    audio = [unpack_frame(p)[1] for p in ws.binary if p[:1] == b"A"]
    assert audio == list(turn.segments)                       # one audio packet per declared segment
    assert any(p[:1] == b"V" for p in ws.binary)
    await coordinator.close()


async def test_speech_events_never_carry_engine_ids_as_turn_ids(tmp_path):
    h, host, ws, binding, coordinator, _ = await rig(tmp_path)
    turn = await coordinator.begin(binding, source="typed")
    assert turn.turn_id != h.engine.turn.turn_id            # chatbot's uuid vs the engine's sequence
    assert host.engine.turn.label == turn.turn_id
    await coordinator.interrupt(binding)
    await coordinator.close()


async def test_barge_in_interrupts_the_chat_turn_and_stops_the_browser(tmp_path):
    h, host, ws, binding, coordinator, _ = await rig(tmp_path)
    cancelled = []
    turn = await coordinator.begin(binding, source="spoken")
    turn.task = asyncio.create_task(asyncio.sleep(30))     # stands in for the LLM generation
    turn.task.add_done_callback(lambda t: (cancelled.append(t.cancelled()), turn.finished.set()))
    coordinator.publish(binding, turn, "A long answer that will be interrupted, midway. And then more")
    await wait_for(lambda: turn.segments)
    h.engine.listening = False
    for _ in range(8):
        await h.engine.feed_microphone(np.full(640, 0.08, "<f4").tobytes())
    await wait_for(lambda: turn.interrupted)
    assert cancelled == [True] and turn.interrupted
    assert any(m.get("type") == "interrupt" and m.get("turn_id") == turn.turn_id for m in ws.json)
    assert h.engine.turn is None and h.engine.listening
    await coordinator.close()


async def test_recognized_speech_starts_exactly_one_chat_turn_and_empty_speech_none(tmp_path):
    h, host, ws, binding, coordinator, spoken = await rig(tmp_path)
    h.transcripts = ["", "tell me a story"]
    for _ in range(2):
        for _ in range(5):
            await h.engine.feed_microphone(np.full(640, 0.08, "<f4").tobytes())
        for _ in range(20):
            await h.engine.feed_microphone(np.zeros(640, "<f4").tobytes())
        await asyncio.sleep(0.1)
    await wait_for(lambda: spoken)
    assert spoken == ["tell me a story"]
    await coordinator.close()


async def test_closing_keeps_models_loaded_but_detaches_the_engine(tmp_path):
    h, host, ws, binding, coordinator, _ = await rig(tmp_path)
    await coordinator.detach(binding)
    assert h.engine.sink is None and h.engine.ready
    assert (h.avatar.stops, h.tts.stops) == (0, 0)
    # A second window attaches to the same warm engine without reloading anything.
    ws2 = FakeSocket()
    second = AvatarBinding("session-1", "user-1", "companion-1", ws2)
    await coordinator.connect(second, host.factory)
    assert (h.avatar.starts, h.tts.starts) == (1, 1) and {"type": "status", "value": "Listening"} in ws2.json
    await host.aclose()
    assert (h.avatar.stops, h.tts.stops) == (1, 1)


async def test_replaced_connection_cannot_cancel_new_owner_or_pause_renderer(tmp_path):
    h, host, first_socket, first, coordinator, _ = await rig(tmp_path)
    second_socket = FakeSocket()
    second = AvatarBinding("session-2", "user-1", "companion-1", second_socket)
    await coordinator.connect(second, host.factory)
    assert h.engine.sink == second.runtime._on_event
    turn = await coordinator.begin(second, source="spoken")
    coordinator.publish(second, turn, "Hello again. ")
    await coordinator.detach(first)
    assert h.engine.sink == second.runtime._on_event
    assert h.engine.turn is not None and h.engine.turn.label == turn.turn_id
    assert not any(m.get("type") == "pause_render" for m in h.avatar.sent)
    coordinator.end(second, turn)
    seen = set()
    for _ in range(300):
        await ack_audio(coordinator, second, second_socket, seen)
        if turn.drained and turn.segments and len(turn.acknowledged) == len(turn.segments):
            break
        await asyncio.sleep(0.01)
    await asyncio.wait_for(coordinator.wait_playback(turn), 3)
    assert any(packet[:1] == b"A" for packet in second_socket.binary)
    assert any(packet[:1] == b"V" for packet in second_socket.binary)
    assert not any(packet[:1] == b"A" for packet in first_socket.binary)
    await coordinator.detach(second)
    assert h.avatar.sent[-1]["type"] == "pause_render"
    assert h.avatar.alive
    await host.aclose()


async def test_repeated_reconnect_pauses_and_resumes_without_reloading(tmp_path):
    h, host, _, first, coordinator, _ = await rig(tmp_path)
    await coordinator.detach(first)
    for index in range(3):
        binding = AvatarBinding(f"reconnect-{index}", "user-1", "companion-1", FakeSocket())
        await coordinator.connect(binding, host.factory)
        assert h.avatar.sent[-1]["type"] == "resume_render"
        await coordinator.detach(binding)
        assert h.avatar.sent[-1]["type"] == "pause_render"
    assert h.avatar.starts == 1 and h.avatar.stops == 0
    await host.aclose()


async def test_replacement_during_synthesis_invalidates_old_turn_without_stopping_new(tmp_path):
    h, host, _, first, coordinator, _ = await rig(tmp_path)
    h.tts.hold = asyncio.Event()
    old_turn = await coordinator.begin(first, source="spoken")
    coordinator.publish(first, old_turn, "A sentence being synthesized.")
    coordinator.end(first, old_turn)
    await wait_for(lambda: any(m.get("type") == "speak" for m in h.tts.sent))
    second_socket = FakeSocket()
    second = AvatarBinding("session-2", "user-1", "companion-1", second_socket)
    await coordinator.connect(second, host.factory)
    assert old_turn.interrupted
    h.tts.hold.set()
    new_turn = await coordinator.begin(second, source="spoken")
    coordinator.publish(second, new_turn, "A new response.")
    coordinator.end(second, new_turn)
    await coordinator.detach(first)
    seen = set()
    for _ in range(300):
        await ack_audio(coordinator, second, second_socket, seen)
        if new_turn.drained and new_turn.segments and len(new_turn.acknowledged) == len(new_turn.segments):
            break
        await asyncio.sleep(0.01)
    await asyncio.wait_for(coordinator.wait_playback(new_turn), 3)
    assert any(packet[:1] == b"A" for packet in second_socket.binary)
    await coordinator.detach(second)
    await host.aclose()
