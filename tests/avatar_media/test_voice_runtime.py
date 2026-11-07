"""Saved voices in the real-time pipeline: per-turn capture, resolution, previews, restarts."""
import asyncio

import numpy as np
import pytest

from chatbot.avatar.media import EngineBusyError, MediaError
from chatbot.avatar.runtime import VoiceResolutionError

from .helpers import TEST_VOICE, Harness, wait_for
from .test_runtime_adapter import rig

VOICE_B = {**TEST_VOICE, "voice_id": "voice-b", "fingerprint": "b" * 64,
           "reference": "library/voice-b/reference.wav", "transcript": "Voice B reference."}


def speaks(h):
    return [m for m in h.tts.sent if m["type"] == "speak"]


async def say(h, text, **kwargs):
    turn = await h.engine.begin_response(**kwargs)
    await h.engine.feed_text(turn, text)
    await h.engine.end_response(turn)
    return turn, await asyncio.wait_for(h.engine.wait_turn(turn), 10)


@pytest.mark.parametrize("mode", ["natural", "fast"])
async def test_every_sentence_of_a_turn_is_spoken_by_that_turns_voice(mode):
    h = await Harness().ready()
    turn, ended = await say(h, "One sentence here. Another sentence there. And a third one.",
                            speech_mode=mode, voice=VOICE_B)
    assert ended.status == "completed"
    assert len(speaks(h)) >= 2 and {m["voice"]["voice_id"] for m in speaks(h)} == {"voice-b"}
    assert all(m["voice"] == {k: VOICE_B[k] for k in VOICE_B} for m in speaks(h))
    segments = [k for k in h.engine.timeline if k.startswith(f"{turn}:")]
    assert segments and all(h.engine.timeline[k]["voice_id"] == "voice-b" for k in segments)


async def test_a_voice_is_required_and_validated_before_anything_is_sent():
    h = await Harness().ready()
    for bad in (None, {}, {**TEST_VOICE, "reference": ""}, "voice-id-only"):
        with pytest.raises(MediaError, match="voice"):
            await h.engine.begin_response(voice=bad)
    assert speaks(h) == [] and h.engine.turn is None


async def test_turn_diagnostics_name_the_voice():
    h = await Harness().ready()
    await say(h, "Hello there.", voice=VOICE_B)
    summary = next(e for e in h.events if type(e).__name__ == "Diagnostic" and e.name == "turn_summary")
    assert summary.data["voice_id"] == "voice-b"


# ---------------------------------------------------------------- through the runtime adapter


async def test_a_voice_change_applies_to_the_next_turn_and_never_mid_turn(tmp_path):
    h, host, ws, binding, coordinator, _ = await rig(tmp_path)
    voices = host.voices
    first = await coordinator.begin(binding, source="typed")
    running = h.engine.turn
    voices.voice = dict(VOICE_B)                       # the user picks another voice mid-reply
    coordinator.publish(binding, first, "First reply, first sentence. Still the first reply. ")
    await wait_for(lambda: len(speaks(h)) >= 1)
    assert running.voice["voice_id"] == "voice-test"
    assert {m["voice"]["voice_id"] for m in speaks(h)} == {"voice-test"}
    await coordinator.interrupt(binding)
    before = len(speaks(h))
    second = await coordinator.begin(binding, source="spoken")
    coordinator.publish(binding, second, "The next reply begins. Then continues")
    await wait_for(lambda: len(speaks(h)) > before)
    assert {m["voice"]["voice_id"] for m in speaks(h)[before:]} == {"voice-b"}
    assert len(voices.calls) >= 3                     # resolved at connect and at each turn start
    await coordinator.close()


@pytest.mark.parametrize("source", ["typed", "spoken"])
async def test_typed_and_spoken_turns_both_resolve_the_conversation_voice(tmp_path, source):
    h, host, ws, binding, coordinator, _ = await rig(tmp_path)
    host.voices.voice = dict(VOICE_B)
    turn = await coordinator.begin(binding, source=source)
    assert h.engine.turn.voice["voice_id"] == "voice-b"
    assert host.voices.calls[-1] == binding.session_id
    await coordinator.close()


async def test_an_unavailable_voice_fails_the_turn_explicitly_and_speaks_nothing(tmp_path):
    h, host, ws, binding, coordinator, _ = await rig(tmp_path)
    host.voices.error = RuntimeError("The voice chosen for this conversation is not available")
    with pytest.raises(VoiceResolutionError, match="not available"):
        await coordinator.begin(binding, source="typed")
    assert speaks(h) == [] and h.engine.turn is None
    assert {"type": "error", "error": "Voice: The voice chosen for this conversation is not available"} in ws.json
    await coordinator.close()


async def test_connecting_prepares_the_conversations_voice_prompt_ahead_of_the_first_turn(tmp_path):
    h, host, ws, binding, coordinator, _ = await rig(tmp_path)
    await wait_for(lambda: any(m["type"] == "prepare" for m in h.tts.sent))
    prepare = next(m for m in h.tts.sent if m["type"] == "prepare")
    assert prepare["voice"]["voice_id"] == "voice-test" and prepare["warm"] is True
    await coordinator.close()


# ---------------------------------------------------------------- voice library requests


async def test_previews_use_the_tts_worker_only_and_never_start_avtr():
    h = Harness()                                    # nothing started: no avatar window open
    result = await h.engine.design_preview("Hello there.", "A calm voice.", "English")
    assert h.tts.starts == 1 and h.avatar.starts == 0
    assert result["model"] == "design" and isinstance(result["audio"], np.ndarray)
    command = next(m for m in h.tts.sent if m["type"] == "design")
    assert (command["text"], command["instruct"], command["language"]) == (
        "Hello there.", "A calm voice.", "English")


async def test_previews_are_refused_while_the_avatar_is_speaking():
    h = await Harness(auto_playback=False).ready()
    turn = await h.engine.begin_response()
    await h.engine.feed_text(turn, "I am in the middle of saying something. ")
    with pytest.raises(EngineBusyError):
        await h.engine.design_preview("Hi.", "A voice.")
    with pytest.raises(EngineBusyError):
        await h.engine.voice_preview("Hi.", VOICE_B)
    assert not [m for m in h.tts.sent if m["type"] == "design"]
    await h.engine.cancel(turn)


async def test_a_barge_in_never_cancels_a_voice_library_request():
    h = await Harness().ready()
    h.tts.hold = asyncio.Event()
    preview = asyncio.create_task(h.engine.voice_preview("Preview text.", VOICE_B))
    await wait_for(lambda: h.engine.tool_pending)
    await h.engine.cancel()                          # an interruption while the preview renders
    h.tts.hold.set()
    result = await asyncio.wait_for(preview, 3)
    assert result["voice_id"] == "voice-b" and result["model"] == "base"


async def test_voice_library_worker_errors_are_reported_not_hidden():
    h = await Harness().ready()
    h.tts.fail_design = "Qwen3-TTS VoiceDesign model failed to load (x): CUDA out of memory"
    with pytest.raises(MediaError, match="CUDA out of memory"):
        await h.engine.design_preview("Hi there.", "A voice.")


async def test_forget_and_unload_reach_the_worker():
    h = await Harness().ready()
    await say(h, "Hello there.", voice=VOICE_B)
    assert ("voice-b", "b" * 64) in h.tts.prompts
    await h.engine.forget_voice("voice-b")
    assert ("voice-b", "b" * 64) not in h.tts.prompts
    await h.engine.design_preview("Hi there.", "A voice.")
    assert await h.engine.unload_design() is True and h.tts.design_loaded is False
    status = await h.engine.tts_status()
    assert status["design_loaded"] is False


async def test_a_restarted_tts_worker_rebuilds_the_voice_from_its_reference():
    h = await Harness().ready()
    first, _ = await say(h, "Before the restart.", voice=VOICE_B)
    assert h.engine.timeline[f"{first}:1"]["prompt_cache"] == "built"
    second, _ = await say(h, "Still cached.", voice=VOICE_B)
    assert h.engine.timeline[f"{second}:1"]["prompt_cache"] == "hit"
    await h.tts.stop()
    h.tts.prompts.clear()                            # a new process has no prompts in memory
    third, ended = await say(h, "After the restart.", voice=VOICE_B)
    assert ended.status == "completed" and h.tts.starts == 2
    assert h.engine.timeline[f"{third}:1"]["prompt_cache"] == "built"
    assert speaks(h)[-1]["voice"]["reference"] == VOICE_B["reference"]
