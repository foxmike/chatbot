"""Real-GPU Voice Library integration: VoiceDesign + Base through the persistent Qwen worker.

Runs only when explicitly requested (it loads two 1.7B checkpoints on the GPU):

    CHATBOT_GPU_TESTS=1 .venv/bin/python -m pytest tests/gpu -s

Set CHATBOT_GPU_TEST_OUTPUT=/some/dir to keep the generated WAV files for listening.
Uses a temporary voice library and no database.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import numpy as np
import pytest

from chatbot.avatar.config import MediaConfig
from chatbot.avatar.media import AvatarMediaEngine
from chatbot.config import Settings
from chatbot.voices.audio import decode_reference, encode_wav, sha256, voice_fingerprint
from chatbot.voices.storage import VoiceStorage

pytestmark = pytest.mark.skipif(os.environ.get("CHATBOT_GPU_TESTS") != "1",
                                reason="set CHATBOT_GPU_TESTS=1 to run the real-GPU Qwen test")

USER = str(uuid.uuid4())
DESCRIPTION = "A warm, natural young adult American woman with a relaxed, expressive conversational voice."
DESIGN_TEXT = ("Hi, it's really good to hear from you. I was just thinking about how quiet the "
               "evening has been, and honestly, I'm glad you reached out.")
UPLOAD_TEXT = "The quick brown fox jumps over the lazy dog. Please read this sentence clearly and slowly."
SENTENCES = ["Good evening, it is lovely to talk with you again.",
             "I have been thinking about what you said yesterday.",
             "Shall we pick up where we left off?"]


def spec(voice_id, stored, transcript):
    return {"voice_id": voice_id, "fingerprint": voice_fingerprint(stored.sha256, transcript),
            "reference": stored.reference_path, "transcript": transcript, "language": "English"}


def keep(name, result):
    target = os.environ.get("CHATBOT_GPU_TEST_OUTPUT")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        (Path(target) / name).write_bytes(encode_wav(result["audio"], result["sr"]))


def gpu_used_mib():
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, check=True).stdout
    return int(out.split()[0])


async def test_voice_design_save_clone_select_switch_and_restart(tmp_path):
    root = tmp_path / "voices"
    config = MediaConfig.from_settings(Settings(_env_file=None))
    config = MediaConfig(**{**config.__dict__, "voice_root": root, "design_idle_seconds": 600.0})
    engine = AvatarMediaEngine(config)
    storage = VoiceStorage(root)
    log = []
    try:
        # 1. VoiceDesign preview (two candidates) --------------------------------------
        started = time.monotonic()
        first = await engine.design_preview(DESIGN_TEXT, DESCRIPTION, "English")
        log.append(f"design preview 1: {first['duration']:.2f}s audio in {time.monotonic() - started:.1f}s "
                   f"(includes Base start-up and VoiceDesign load)")
        started = time.monotonic()
        second = await engine.design_preview(DESIGN_TEXT, DESCRIPTION, "English")
        log.append(f"design preview 2: {second['duration']:.2f}s audio in {time.monotonic() - started:.1f}s")
        assert first["model"] == second["model"] == "design"
        assert first["duration"] > 3.0 and np.max(np.abs(first["audio"])) > 0.05
        assert not np.array_equal(first["audio"], second["audio"])     # independent candidates
        keep("design_candidate_1.wav", first)
        keep("design_candidate_2.wav", second)
        status = await engine.tts_status()
        assert status["design_loaded"] and status["base_loaded"]
        assert (status["base_loads"], status["design_loads"]) == (1, 1)
        both_resident = status["reserved_mib"]

        # 2. Save the approved candidate (candidate 2 is rejected and discarded) --------
        approved_wav = encode_wav(first["audio"], first["sr"])
        approved = storage.create_preview(USER, "design", approved_wav, {"transcript": DESIGN_TEXT})
        rejected = storage.create_preview(USER, "design", encode_wav(second["audio"], second["sr"]), {})
        storage.delete_preview(USER, rejected["preview_id"])
        designed_id = str(uuid.uuid4())
        designed = storage.promote(USER, approved["preview_id"], designed_id)
        assert storage.reference_file(designed.reference_path).read_bytes() == approved_wav
        designed_voice = spec(designed_id, designed, DESIGN_TEXT)

        released = await engine.unload_design()
        status = await engine.tts_status()
        assert released and not status["design_loaded"] and status["base_loaded"]
        log.append(f"PyTorch reserved: {both_resident:.0f} MiB with Base+VoiceDesign, "
                   f"{status['reserved_mib']:.0f} MiB after releasing VoiceDesign")
        assert status["reserved_mib"] < both_resident - 1000

        # 3. Base cloning from the designed reference -----------------------------------
        replies = []
        for n, sentence in enumerate(SENTENCES, 1):
            started = time.monotonic()
            reply = await engine.voice_preview(sentence, designed_voice)
            replies.append(reply)
            keep(f"designed_voice_sentence_{n}.wav", reply)
            log.append(f"designed voice sentence {n}: prompt {reply['prompt']}, {reply['duration']:.2f}s audio, "
                       f"synthesis {reply['generation']:.2f}s")
        assert [r["model"] for r in replies] == ["base"] * 3
        assert [r["prompt"] for r in replies] == ["built", "hit", "hit"]
        assert {r["fingerprint"] for r in replies} == {designed_voice["fingerprint"]}   # one identity

        # 4. A user-uploaded recording (espeak-ng stands in for a person) ---------------
        if shutil.which("espeak-ng") is None:
            pytest.skip("espeak-ng is needed to make an upload recording")
        recording = tmp_path / "upload.wav"
        subprocess.run(["espeak-ng", "-v", "en-us", "-s", "140", "-w", str(recording), UPLOAD_TEXT], check=True)
        reference = decode_reference(recording.read_bytes(), "upload.wav", max_bytes=20 * 1024 * 1024,
                                     min_seconds=3, max_seconds=30)
        upload = storage.create_preview(USER, "clone", encode_wav(reference.samples, reference.sample_rate),
                                        {"transcript": UPLOAD_TEXT})
        clone_preview_voice = {"voice_id": f"preview-{upload['preview_id']}",
                               "fingerprint": voice_fingerprint(upload["reference_sha256"], UPLOAD_TEXT),
                               "reference": storage.preview_reference_relative(USER, upload["preview_id"]),
                               "transcript": UPLOAD_TEXT, "language": "English"}
        sample = await engine.voice_preview("Does this sound like the recording?", clone_preview_voice)
        assert sample["model"] == "base" and sample["duration"] > 0.5
        keep("upload_clone_preview.wav", sample)
        cloned_id = str(uuid.uuid4())
        cloned = storage.promote(USER, upload["preview_id"], cloned_id)
        cloned_voice = spec(cloned_id, cloned, UPLOAD_TEXT)

        # 5-7. Select each voice and switch back and forth: only the prompt changes ------
        order = [cloned_voice, designed_voice, cloned_voice, designed_voice]
        for n, voice in enumerate(order, 1):
            reply = await engine.voice_preview(f"Switch number {n}.", voice)
            assert reply["voice_id"] == voice["voice_id"] and reply["model"] == "base"
            keep(f"switch_{n}_{'cloned' if voice is cloned_voice else 'designed'}.wav", reply)
        status = await engine.tts_status()
        assert status["base_loads"] == 1, "switching voices must not reload the Base checkpoint"
        assert status["design_loads"] == 1 and not status["design_loaded"]
        cached = {(p["voice_id"], p["fingerprint"]) for p in status["cached_prompts"]}
        assert {(designed_id, designed_voice["fingerprint"]), (cloned_id, cloned_voice["fingerprint"])} <= cached
        log.append(f"after {len(order)} voice switches: Base loaded {status['base_loads']}x, "
                   f"{len(status['cached_prompts'])} prompts cached, reserved {status['reserved_mib']:.0f} MiB")

        # 8. The worker restarts: the voice is rebuilt from its saved reference ---------
        await engine.tts.stop()
        started = time.monotonic()
        reply = await engine.voice_preview(SENTENCES[0], designed_voice)
        log.append(f"after a worker restart: prompt {reply['prompt']} in {time.monotonic() - started:.1f}s "
                   f"(includes reloading Base)")
        assert reply["prompt"] == "built" and reply["fingerprint"] == designed_voice["fingerprint"]
        assert sha256(storage.reference_file(designed.reference_path).read_bytes()) == designed.sha256
        keep("designed_voice_after_restart.wav", reply)
    finally:
        await engine.tts.stop()
        for task in engine._pumps:   # noqa: SLF001 - test teardown of its own engine
            task.cancel()
        await asyncio.gather(*engine._pumps, return_exceptions=True)  # noqa: SLF001
        print("\n" + "\n".join(f"  {line}" for line in log))
