"""Media engine behaviour on fake Qwen/AVTR workers: ordering, overlap, interruption, portraits."""
import asyncio
import base64
import time

import numpy as np
import pytest

from chatbot.avatar.media import MAX_AUDIO_CHUNKS, AvatarMediaEngine, MediaError
from chatbot.avatar.protocol import (
    Interrupted,
    TranscriptReady,
    UserSpeechStarted,
    unpack_frame,
)

from .helpers import SR, Harness, wait_for

TEXT = " ".join(f"This is sentence number {n}." for n in range(12))


async def speak(harness, text=TEXT, per_token_delay=0.0):
    engine = harness.engine
    turn = await engine.begin_response()
    for word in text.split(" "):
        await engine.feed_text(turn, word + " ")
        await asyncio.sleep(per_token_delay)
    await engine.end_response(turn)
    return turn, await asyncio.wait_for(engine.wait_turn(turn), 10)


async def test_incremental_text_becomes_ordered_bounded_speech():
    h = await Harness().ready()
    in_flight = 0
    original = h.avatar.react

    async def watch(message):
        nonlocal in_flight
        if message["type"] == "speak":
            in_flight = max(in_flight, sum(not f.done() for f in h.engine.playback_pending.values()))
        await original(message)
    h.avatar.react = watch
    turn, ended = await speak(h)
    sent = [m["id"] for m in h.avatar.sent if m["type"] == "speak"]
    assert ended.status == "completed" and ended.spoken_text == TEXT.strip()
    assert sent == [f"{turn}:{n}" for n in range(1, len(sent) + 1)]
    assert len(sent) > 1 and in_flight <= MAX_AUDIO_CHUNKS
    # Each speech chunk's audio frame precedes its first video frame and keeps the wire format.
    audio_ids = [unpack_frame(e.data)[1] for e in h.frames("audio")]
    assert audio_ids == sent
    first_audio = next(i for i, e in enumerate(h.events) if getattr(e, "kind", "") == "audio")
    assert getattr(h.events[first_audio + 1], "kind", "") == "video"
    assert "Listening" == h.statuses()[-1] and h.engine.listening and h.engine.turn is None


async def test_synthesis_overlaps_playback_and_llm_arrival():
    h = await Harness(playback_delay=0.15).ready()
    overlapped_playback = overlapped_llm = False
    original = h.tts.react

    async def watch(message):
        nonlocal overlapped_playback, overlapped_llm
        overlapped_playback |= any(not f.done() for f in h.engine.playback_pending.values())
        overlapped_llm |= not h.engine.turn.input_closed
        await original(message)
    h.tts.react = watch
    await speak(h, per_token_delay=0.002)
    assert overlapped_playback and overlapped_llm


async def test_wire_audio_and_video_bytes_match_the_browser_protocol():
    h = await Harness().ready()
    await speak(h, "Hello there, my friend.")
    audio = h.frames("audio")[0]
    tag, segment_id, sr, pcm = unpack_frame(audio.data)
    assert tag == b"A" and sr == SR and segment_id.endswith(":1") and len(pcm) % 4 == 0
    assert audio.data[1:3] == len(segment_id).to_bytes(2, "little")
    video = h.frames("video")[0]
    tag, vid, pts, jpeg = unpack_frame(video.data)
    assert tag == b"V" and vid == segment_id and pts == 0.0 and jpeg == b"\xff\xd8jpeg"


async def test_reasoning_tags_and_markup_never_reach_tts():
    h = await Harness().ready()
    await speak(h, "<think>secret plan</think>Hello **there**, friend.")
    spoken = " ".join(m["text"] for m in h.tts.sent if m["type"] == "speak")
    assert spoken == "Hello there, friend." and "secret" not in spoken


async def test_tts_receives_the_exact_configured_voice():
    h = await Harness().ready()
    await speak(h, "Hello there, my friend.")
    command = next(m for m in h.tts.sent if m["type"] == "speak")
    assert command["voice"] == ("A warm, natural young adult American woman with a relaxed, "
                                "expressive conversational voice.")
    assert command["mode"] == "design"


async def test_silence_is_trimmed_before_audio_is_sent():
    h = await Harness().ready()
    await speak(h, "Hello there, my friend.")
    pcm = unpack_frame(h.frames("audio")[0].data)[3]
    seconds = len(pcm) / 4 / SR
    speech = h.tts.speech_seconds("Hello there, my friend.")
    # 0.3 s lead -> 12 ms; 0.4 s tail -> 140 ms (sentence end); plus a planner pause of at most 260 ms.
    assert speech + 0.152 <= seconds <= speech + 0.152 + 0.27


async def test_barge_in_cancels_turn_clears_queues_and_discards_stale_synthesis():
    h = await Harness(auto_playback=False).ready()
    h.tts.hold = asyncio.Event()                      # Qwen is busy when the user interrupts
    turn = await h.engine.begin_response()
    await h.engine.feed_text(turn, "Let me tell you a long story about the sea, and the sky. ")
    await wait_for(lambda: h.engine.tts_pending)
    stale_id = next(iter(h.engine.tts_pending))
    h.engine.listening = False
    loud = np.full(640, 0.08, "<f4").tobytes()
    before = len(h.events)
    for _ in range(8):
        await h.engine.feed_microphone(loud)
    ended = await asyncio.wait_for(h.engine.wait_turn(turn), 2)
    assert ended.status == "cancelled"
    new = h.events[before:]
    interrupted = [e for e in new if isinstance(e, Interrupted)]
    assert interrupted == [Interrupted("barge-in", turn)]
    assert any(isinstance(e, UserSpeechStarted) and e.barge_in for e in new)
    assert any(getattr(e, "payload", {}).get("type") == "interrupt" for e in new)
    assert {"type": "interrupt"} in h.avatar.sent
    assert not h.engine.tts_pending and not h.engine.audio_pending and not h.engine.playback_pending
    assert h.engine.turn is None and h.engine.listening
    # Qwen finishes later: its waveform must be dropped, not sent to AVTR or the browser.
    h.tts.hold.set()
    await asyncio.gather(*h.tts.replies)
    await asyncio.sleep(0.05)
    assert not [m for m in h.avatar.sent if m["type"] == "speak"]
    assert not h.frames("audio")
    assert stale_id not in h.engine.audio_pending


async def test_stale_avtr_frames_after_interrupt_are_not_forwarded():
    h = await Harness(auto_playback=False).ready()
    turn = await h.engine.begin_response()
    await h.engine.feed_text(turn, "Hello there, my friend. ")
    await h.engine.end_response(turn)
    await wait_for(lambda: h.frames("audio"))
    await h.engine.cancel(turn)
    shown = len(h.frames("video"))
    h.avatar.reply({"type": "frame", "kind": "speech", "id": f"{turn}:1", "first": False, "pts": 0.5,
                    "generated_at": 1.0, "jpeg": base64.b64encode(b"late").decode()})
    h.avatar.reply({"type": "audio_received", "id": f"{turn}:1", "duration": 1.0, "at": 2.0})
    await asyncio.sleep(0.05)
    assert len(h.frames("video")) == shown
    assert f"{turn}:1" not in h.engine.avatar_audio_received
    # Idle frames (empty id) still flow so the avatar returns to rest.
    h.avatar.reply({"type": "frame", "kind": "listening", "id": None, "first": True, "pts": 0,
                    "generated_at": 1.0, "jpeg": base64.b64encode(b"idle").decode()})
    await wait_for(lambda: len(h.frames("video")) == shown + 1)


async def test_next_turn_works_after_interruption_and_ids_are_not_reused():
    h = await Harness(auto_playback=True).ready()
    turn1 = await h.engine.begin_response()
    await h.engine.feed_text(turn1, "A first answer that gets interrupted, midway. ")
    await wait_for(lambda: h.tts.sent)
    await h.engine.cancel(turn1)
    assert await h.engine.feed_text(turn1, "late tokens") is False
    turn2, ended = await speak(h, "Here is the second answer.")
    assert turn2 != turn1 and ended.status == "completed"
    delivered = [unpack_frame(e.data)[1] for e in h.frames("audio")]
    assert delivered and all(i.startswith(f"{turn2}:") for i in delivered)
    assert h.engine.planner is not None and h.engine.listening


async def test_cancel_before_runner_starts_still_settles_wait_turn():
    h = await Harness().ready()
    turn = await h.engine.begin_response()
    await h.engine.cancel(turn)
    ended = await asyncio.wait_for(h.engine.wait_turn(turn), 1)
    assert ended.status == "cancelled"


async def test_tts_error_fails_the_turn_and_returns_to_listening():
    h = await Harness().ready()
    original = h.tts.react

    async def fail(message):
        if message["type"] == "speak":
            h.tts.reply({"type": "error", "id": message["id"], "error": "inference failed"})
        else:
            await original(message)
    h.tts.react = fail
    turn, ended = await speak(h, "Hello there, my friend.")
    assert ended.status == "failed" and "inference failed" in ended.error
    assert h.engine.listening and h.engine.turn is None
    assert any("inference failed" in s for s in h.statuses())


async def test_avtr_failure_mid_turn_fails_it():
    h = await Harness(auto_playback=False).ready()
    turn = await h.engine.begin_response()
    await h.engine.feed_text(turn, "Hello there, my friend. ")
    await h.engine.end_response(turn)
    await wait_for(lambda: h.engine.playback_pending)
    h.avatar.reply({"type": "error", "error": "AVTR exited"})
    ended = await asyncio.wait_for(h.engine.wait_turn(turn), 3)
    assert ended.status == "failed" and "AVTR exited" in ended.error


async def test_feed_text_applies_backpressure_when_too_much_text_is_pending():
    h = await Harness().ready()
    h.tts.hold = asyncio.Event()
    turn = await h.engine.begin_response()
    task = asyncio.create_task(h.engine.feed_text(turn, "word " * 400))
    await asyncio.sleep(0.2)
    assert not task.done()                                  # blocked: engine saturated
    h.tts.hold.set()
    assert await asyncio.wait_for(task, 5) is True
    await h.engine.end_response(turn)
    assert (await asyncio.wait_for(h.engine.wait_turn(turn), 10)).status == "completed"


async def test_begin_response_requires_a_portrait():
    h = await Harness().ready(portrait=None)
    with pytest.raises(MediaError, match="portrait"):
        await h.engine.begin_response()


async def test_new_turn_supersedes_an_unfinished_one():
    h = await Harness().ready()
    first = await h.engine.begin_response()
    second = await h.engine.begin_response()
    assert (await h.engine.wait_turn(first)).status == "cancelled"
    assert h.engine.turn.turn_id == second
    await h.engine.cancel()


# ---------------------------------------------------------------- microphone -> transcript


def voiced(n=5):
    return [np.full(640, 0.08, "<f4").tobytes()] * n


async def test_endpoint_waits_for_configured_silence_then_delivers_transcript():
    h = await Harness().ready()
    h.transcripts = ["tell me about it"]
    quiet = np.zeros(640, "<f4").tobytes()
    for packet in voiced():
        await h.engine.feed_microphone(packet)
    assert h.engine.endpointer.speaking and h.engine.listening
    for _ in range(int(h.engine.config.endpoint_silence / 0.04) - 1):
        await h.engine.feed_microphone(quiet)
    assert h.engine.endpointer.speaking
    for _ in range(2):
        await h.engine.feed_microphone(quiet)
    assert not h.engine.endpointer.speaking and not h.engine.listening
    await wait_for(lambda: h.of(TranscriptReady))
    transcript = h.of(TranscriptReady)[0]
    assert transcript.text == "tell me about it" and transcript.endpoint_delay is not None
    assert h.avatar.sent[-1]["type"] in ("listen", "portrait")
    assert any(m["type"] == "listen" for m in h.avatar.sent)   # idle avatar listened


async def test_empty_transcript_returns_to_listening():
    h = await Harness().ready()
    h.transcripts = [""]
    for packet in voiced():
        await h.engine.feed_microphone(packet)
    for _ in range(20):
        await h.engine.feed_microphone(np.zeros(640, "<f4").tobytes())
    await wait_for(lambda: h.of(TranscriptReady))
    assert h.of(TranscriptReady)[0].text == "" and h.engine.listening


async def test_stt_never_runs_on_the_event_loop_thread():
    import threading
    h = await Harness().ready()
    threads = []
    model = h.engine.transcriber._model
    original = model.transcribe

    def spy(audio, **kw):
        threads.append(threading.current_thread())
        time.sleep(0.15)
        return original(audio, **kw)
    model.transcribe = spy
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1
    beat = asyncio.create_task(heartbeat())
    for packet in voiced():
        await h.engine.feed_microphone(packet)
    for _ in range(20):
        await h.engine.feed_microphone(np.zeros(640, "<f4").tobytes())
    await wait_for(lambda: h.of(TranscriptReady))
    beat.cancel()
    assert threads and threads[0] is not threading.main_thread() and ticks >= 8


async def test_speaking_during_stt_interrupts_the_pending_transcript():
    h = await Harness().ready()
    h.engine.listening = False
    h.engine._utterance_info = {"speech_end": time.monotonic()}   # transcript delivered, LLM thinking
    for packet in voiced(8):
        await h.engine.feed_microphone(packet)
    assert [e.reason for e in h.of(Interrupted)] == ["barge-in"] and h.of(Interrupted)[0].turn_id is None
    assert h.engine.listening


async def test_barge_in_can_be_disabled():
    h = await Harness(barge_in_enabled=False).ready()
    h.engine.listening = False
    for packet in voiced(10):
        await h.engine.feed_microphone(packet)
    assert not h.of(UserSpeechStarted) and not h.of(Interrupted)


async def test_disabled_microphone_is_ignored():
    h = await Harness().ready()
    h.engine.set_microphone_enabled(False)
    for packet in voiced(10):
        await h.engine.feed_microphone(packet)
    assert not h.engine.endpointer.speaking and not h.of(UserSpeechStarted)


# ---------------------------------------------------------------- browser feedback / planner


async def test_browser_schedule_feedback_corrects_the_planner_and_reports_diagnostics():
    h = await Harness(auto_playback=False).ready()
    turn = await h.engine.begin_response()
    planner = h.engine.planner
    planner.audio_ready(f"{turn}:1", time.monotonic(), 2.0)
    estimate = planner.horizon
    now_ctx = 100.0
    await h.engine.handle_client_message({"type": "segment_scheduled", "id": f"{turn}:1", "ctx_now": now_ctx,
                                          "begin": now_ctx + 0.06, "end": now_ctx + 2.5, "prev_end": 0.0})
    assert planner.horizon != estimate
    names = [e.name for e in h.of(__import__("chatbot.avatar.protocol", fromlist=["Diagnostic"]).Diagnostic)]
    assert "segment_scheduled" in names
    await h.engine.cancel()


# ---------------------------------------------------------------- portraits


async def test_invalid_portraits_are_rejected_gracefully_and_keep_the_current_one(tmp_path):
    h = await Harness().ready()
    current = h.engine.portrait_id
    missing = await h.engine.set_portrait(tmp_path / "nope.png")
    notimage = tmp_path / "x.txt"
    notimage.write_text("hello")
    text = await h.engine.set_portrait(notimage)
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    zero = await h.engine.set_portrait(empty)
    faceless = h.make_portrait(tmp_path / "faceless.png")
    h.avatar.reject.add(str(faceless.resolve()))
    rejected = await h.engine.set_portrait(faceless)
    for result in (missing, text, zero, rejected):
        assert not result.ok and result.error
    assert "no face" in rejected.error
    assert h.engine.portrait_id == current


async def test_switching_portraits_reuses_workers_and_reports_cache_hits(tmp_path):
    h = await Harness().ready()
    a, b = h.make_portrait(tmp_path / "a.png"), h.make_portrait(tmp_path / "b.jpg", b"\xff\xd8\xff")
    first = await h.engine.set_portrait(a, "char-a")
    second = await h.engine.set_portrait(b, "char-b")
    back = await h.engine.set_portrait(a, "char-a")
    again = await h.engine.set_portrait(a, "char-a")
    assert [r.ok for r in (first, second, back, again)] == [True] * 4
    assert (first.cached, second.cached, back.cached) == (False, False, True)
    assert again.switched is False and h.engine.portrait_id == "char-a"
    assert h.avatar.starts == 1 and h.tts.starts == 1       # no worker was restarted


async def test_portrait_change_is_refused_during_a_response(tmp_path):
    h = await Harness().ready()
    await h.engine.begin_response()
    result = await h.engine.set_portrait(h.make_portrait(tmp_path / "c.png"), "char-c")
    assert not result.ok and "in progress" in result.error
    await h.engine.cancel()
    assert (await h.engine.set_portrait(tmp_path / "c.png", "char-c")).ok


async def test_portrait_load_failure_in_avtr_is_reported_not_raised(tmp_path):
    h = await Harness().ready()
    path = h.make_portrait(tmp_path / "d.png")

    async def crash(message):
        if message["type"] == "portrait":
            h.avatar.reply({"type": "error", "error": "CUDA out of memory"})
    h.avatar.react = crash
    result = await h.engine.set_portrait(path, "char-d")
    assert not result.ok and "CUDA" in result.error


# ---------------------------------------------------------------- lifecycle


async def test_start_is_idempotent_and_loads_each_model_once():
    h = Harness()
    await asyncio.gather(h.engine.start(), h.engine.start(), h.engine.start())
    await h.engine.start()
    assert (h.avatar.starts, h.tts.starts) == (1, 1) and h.engine.ready and h.engine.transcriber.loaded


async def test_models_stay_loaded_between_turns():
    h = await Harness().ready()
    await speak(h, "First answer, please.")
    await speak(h, "Second answer, please.")
    assert (h.avatar.starts, h.tts.starts, h.avatar.stops, h.tts.stops) == (1, 1, 0, 0)


async def test_begin_response_starts_the_engine_on_demand():
    h = Harness()
    assert not h.engine.ready
    with pytest.raises(MediaError, match="portrait"):
        await h.engine.begin_response()
    assert h.engine.ready


async def test_stop_releases_workers_and_cancels_in_flight_work():
    h = await Harness(auto_playback=False).ready()
    turn = await h.engine.begin_response()
    await h.engine.feed_text(turn, "Hello there, my friend. ")
    await h.engine.stop()
    assert (h.avatar.stops, h.tts.stops) == (1, 1) and not h.engine.ready
    assert (await h.engine.wait_turn(turn)).status == "cancelled"
    assert not h.engine._pumps


async def test_a_crashed_worker_is_restarted_on_next_use_without_touching_the_other():
    h = await Harness().ready()
    h.avatar._alive = False                      # AVTR died
    h.avatar.reply({"type": "error", "error": "AVTR exited"})
    await asyncio.sleep(0.02)
    await h.engine.start()
    assert (h.avatar.starts, h.tts.starts) == (2, 1)
    assert h.engine.portrait_id is None          # fresh AVTR: portrait must be re-selected


async def test_failed_start_cleans_up_both_workers():
    h = Harness()
    h.tts.fail_start = RuntimeError("Qwen failed to load")
    with pytest.raises(RuntimeError, match="Qwen"):
        await h.engine.start()
    assert h.avatar.stops == 1 and not h.engine.ready


async def test_missing_dependency_is_reported_at_start_not_mid_conversation(monkeypatch):
    import builtins
    real = builtins.__import__

    def deny(name, *a, **k):
        if name.startswith("scipy"):
            raise ImportError("no scipy")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", deny)
    from chatbot.avatar.media import default_resampler
    engine = AvatarMediaEngine(worker_factory=lambda _: (Harness().avatar, Harness().tts),
                               resampler=default_resampler)
    with pytest.raises(MediaError, match="scipy"):
        await engine.start()
