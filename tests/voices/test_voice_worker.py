"""voice_worker.py: model lifecycle, clone-prompt cache, reference safety (fake Qwen models)."""
import base64
import importlib.util
import os
from pathlib import Path

import numpy as np
import pytest

from chatbot.voices.audio import encode_wav, read_wav, voice_fingerprint

WORKERS = Path(__file__).resolve().parents[2] / "chatbot" / "avatar" / "worker_scripts"
BASE = "/models/Qwen3-TTS-12Hz-1.7B-Base"
DESIGN = "/models/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
SR = 24000


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vw = load("voice_worker_under_test", WORKERS / "voice_worker.py")
reference_worker = load("tts_worker_reference", WORKERS / "tts_worker.py")


class FakeQwen:
    def __init__(self, checkpoint):
        self.checkpoint = checkpoint
        self.kind = "base" if checkpoint.endswith("Base") else "design"
        self.prompts, self.clones, self.designs = [], [], []
        self.seconds = 1.0           # length of generated speech

    def _wav(self):
        return [np.full(int(self.seconds * SR), 0.1, np.float32)], SR

    def create_voice_clone_prompt(self, ref_audio, ref_text, x_vector_only_mode):
        assert self.kind == "base", "only Base builds clone prompts"
        assert isinstance(ref_audio, tuple) and isinstance(ref_audio[0], np.ndarray), \
            "references must reach Qwen as (waveform, sr), never as a path/URL string"
        self.prompts.append({"samples": len(ref_audio[0]), "sr": ref_audio[1], "ref_text": ref_text,
                             "x_vector_only_mode": x_vector_only_mode})
        return [f"prompt:{ref_text}:{len(ref_audio[0])}"]

    def generate_voice_clone(self, text, language, voice_clone_prompt, max_new_tokens):
        assert self.kind == "base"
        self.clones.append({"text": text, "language": language, "prompt": voice_clone_prompt,
                            "max_new_tokens": max_new_tokens})
        return self._wav()

    def generate_voice_design(self, text, language, instruct, max_new_tokens):
        assert self.kind == "design"
        self.designs.append({"text": text, "language": language, "instruct": instruct,
                             "max_new_tokens": max_new_tokens})
        return self._wav()


class Rig:
    def __init__(self, root, idle=90.0):
        self.root = Path(root)
        self.models, self.loads, self.releases = {}, [], 0
        self.now = 1000.0
        self.fail = set()
        self.service = vw.VoiceTTSService(
            BASE, DESIGN, self.root, load_model=self.load_model, read_audio=self.read_audio,
            release=self.release, clock=lambda: self.now, design_idle_seconds=idle)

    def load_model(self, checkpoint):
        self.loads.append(checkpoint)
        if checkpoint in self.fail:
            raise RuntimeError("CUDA out of memory")
        self.models[checkpoint] = FakeQwen(checkpoint)
        return self.models[checkpoint]

    def read_audio(self, path):
        assert Path(path).resolve().is_relative_to(self.root.resolve())
        return read_wav(Path(path).read_bytes())

    def release(self):
        self.releases += 1

    @property
    def base(self):
        return self.models[BASE]

    def voice(self, voice_id="v1", transcript="Reference words.", seconds=4.0, name="reference.wav"):
        path = self.root / "library" / voice_id / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = encode_wav(np.sin(np.linspace(0, 900, int(seconds * SR))).astype(np.float32) * 0.5, SR)
        path.write_bytes(data)
        from chatbot.voices.audio import sha256
        return {"voice_id": voice_id, "fingerprint": voice_fingerprint(sha256(data), transcript),
                "reference": f"library/{voice_id}/{name}", "transcript": transcript, "language": "English"}

    def run(self, command):
        replies = []
        self.service.handle(command, lambda **m: replies.append(m))
        return replies[0] if replies else None


@pytest.fixture
def rig(tmp_path):
    rig = Rig(tmp_path)
    rig.service.load_base()
    return rig


def test_reuses_the_proven_runaway_guard_unchanged():
    assert vw.length_cap is not None and vw.FRAME_RATE == reference_worker.FRAME_RATE == 12.5
    assert vw.length_cap("x" * 80) == reference_worker.length_cap("x" * 80)


# ---------------------------------------------------------------- model lifecycle


def test_base_is_loaded_at_start_and_voicedesign_only_on_demand(rig):
    assert rig.loads == [BASE]
    assert rig.service.status()["design_loaded"] is False
    reply = rig.run({"type": "design", "id": "d1", "text": "Hello there, nice to meet you.",
                     "instruct": "A calm, low voice.", "language": "English"})
    assert reply["type"] == "audio" and reply["model"] == "design"
    assert rig.loads == [BASE, DESIGN]
    design = rig.models[DESIGN].designs[0]
    assert (design["text"], design["instruct"]) == ("Hello there, nice to meet you.", "A calm, low voice.")
    assert rig.base.clones == []                  # VoiceDesign creates voices; Base was not used


def test_voicedesign_is_released_when_idle_and_base_stays_warm(rig):
    rig.run({"type": "design", "id": "d1", "text": "One candidate.", "instruct": "Bright.", "language": "English"})
    rig.now += 30
    assert rig.service.tick() is False and rig.service.design is not None   # still in use
    rig.run({"type": "design", "id": "d2", "text": "Another candidate.", "instruct": "Bright.",
             "language": "English"})
    assert rig.loads.count(DESIGN) == 1           # regenerating candidates does not reload
    rig.now += 91
    assert rig.service.tick() is True
    assert rig.service.design is None and rig.releases == 1
    assert rig.service.base is not None and rig.loads.count(BASE) == 1


def test_explicit_unload_releases_only_voicedesign(rig):
    voice = rig.voice()
    rig.run({"type": "speak", "id": "s1", "text": "Hi.", "voice": voice})
    rig.run({"type": "design", "id": "d1", "text": "Candidate.", "instruct": "Warm.", "language": "English"})
    reply = rig.run({"type": "unload_design", "id": "u1"})
    assert reply == {"type": "design_unloaded", "id": "u1", "released": True, "reason": "request"}
    status = rig.run({"type": "status", "id": "st"})
    assert status["design_loaded"] is False and status["base_loaded"] is True
    assert status["cached_prompts"] == [{"voice_id": "v1", "fingerprint": voice["fingerprint"]}]


def test_voicedesign_load_failure_is_an_explicit_error_with_no_fallback(rig):
    rig.fail.add(DESIGN)
    reply = rig.run({"type": "design", "id": "d1", "text": "Candidate.", "instruct": "Warm.",
                     "language": "English"})
    assert reply["type"] == "error" and reply["id"] == "d1"
    assert "VoiceDesign model failed to load" in reply["error"] and "CUDA out of memory" in reply["error"]
    assert rig.base.clones == [] and rig.base.prompts == []   # never answered by another model


def test_base_load_failure_is_explicit(tmp_path):
    rig = Rig(tmp_path)
    rig.fail.add(BASE)
    with pytest.raises(vw.VoiceWorkerError, match="Base model failed to load"):
        rig.service.load_base()


# ---------------------------------------------------------------- speaking with saved voices


def test_speech_uses_base_with_the_voices_reference_and_transcript(rig):
    voice = rig.voice(transcript="Exactly these reference words.", seconds=5.0)
    reply = rig.run({"type": "speak", "id": "1:1", "text": "Good evening.", "voice": voice})
    assert reply["type"] == "audio" and reply["model"] == "base" and reply["prompt"] == "built"
    assert reply["voice_id"] == "v1" and reply["fingerprint"] == voice["fingerprint"]
    prompt = rig.base.prompts[0]
    assert prompt["ref_text"] == "Exactly these reference words."
    assert prompt["samples"] == 5 * SR and prompt["sr"] == SR
    assert prompt["x_vector_only_mode"] is False      # full (ICL) cloning, not embedding-only
    clone = rig.base.clones[0]
    assert clone["text"] == "Good evening." and clone["language"] == "English"
    assert clone["prompt"] == ["prompt:Exactly these reference words.:120000"]
    assert DESIGN not in rig.loads
    audio = np.frombuffer(base64.b64decode(reply["audio"]), "<f4")
    assert len(audio) == SR and reply["duration"] == pytest.approx(1.0)


def test_the_clone_prompt_is_built_once_and_reused_for_every_sentence(rig):
    voice = rig.voice()
    caches = [rig.run({"type": "speak", "id": f"1:{n}", "text": f"Sentence {n}.", "voice": voice})["prompt"]
              for n in range(1, 6)]
    assert caches == ["built", "hit", "hit", "hit", "hit"]
    assert len(rig.base.prompts) == 1
    assert {c["prompt"][0] for c in rig.base.clones} == {rig.base.clones[0]["prompt"][0]}


def test_switching_voices_changes_only_the_prompt_never_the_model(rig):
    a, b = rig.voice("va", "Voice A words."), rig.voice("vb", "Voice B words.")
    for voice in (a, b, a, b):
        rig.run({"type": "speak", "id": "x", "text": "Hello.", "voice": voice})
    assert rig.loads == [BASE]                  # no checkpoint reload when switching voices
    assert len(rig.base.prompts) == 2           # one prompt per voice, then reused
    assert [c["prompt"][0].split(":")[1] for c in rig.base.clones] == [
        "Voice A words.", "Voice B words.", "Voice A words.", "Voice B words."]


def test_a_changed_reference_or_transcript_invalidates_the_cached_prompt(rig):
    voice = rig.voice(transcript="Old transcript.")
    rig.run({"type": "speak", "id": "1", "text": "Hi.", "voice": voice})
    corrected = {**voice, "transcript": "Corrected transcript.",
                 "fingerprint": voice_fingerprint("same-audio", "Corrected transcript.")}
    reply = rig.run({"type": "speak", "id": "2", "text": "Hi.", "voice": corrected})
    assert reply["prompt"] == "built"
    assert rig.base.prompts[-1]["ref_text"] == "Corrected transcript."
    assert [p["fingerprint"] for p in rig.service.status()["cached_prompts"]] == [corrected["fingerprint"]]


def test_forget_drops_a_voices_prompts(rig):
    voice = rig.voice()
    rig.run({"type": "speak", "id": "1", "text": "Hi.", "voice": voice})
    assert rig.run({"type": "forget", "id": "f", "voice_id": "v1"})["dropped"] == 1
    assert rig.run({"type": "speak", "id": "2", "text": "Hi.", "voice": voice})["prompt"] == "built"


def test_prompt_cache_is_bounded(tmp_path):
    rig = Rig(tmp_path)
    rig.service.max_prompts = 2
    for name in ("a", "b", "c"):
        rig.run({"type": "speak", "id": name, "text": "Hi.", "voice": rig.voice(name)})
    assert [p["voice_id"] for p in rig.service.status()["cached_prompts"]] == ["b", "c"]


def test_a_restarted_worker_rebuilds_prompts_from_the_saved_reference(tmp_path):
    first = Rig(tmp_path)
    voice = first.voice(transcript="Persistent reference.")
    first.run({"type": "speak", "id": "1", "text": "Hi.", "voice": voice})
    restarted = Rig(tmp_path)                    # new process: nothing in memory
    reply = restarted.run({"type": "speak", "id": "2", "text": "Hi again.", "voice": voice})
    assert reply["type"] == "audio" and reply["prompt"] == "built"
    assert restarted.base.prompts[0]["ref_text"] == "Persistent reference."
    assert restarted.base.prompts[0]["samples"] == first.base.prompts[0]["samples"]


def test_prepare_builds_the_prompt_and_can_warm_the_model(rig):
    voice = rig.voice()
    reply = rig.run({"type": "prepare", "id": "p", "voice": voice, "warm": True})
    assert reply["type"] == "prepared" and reply["prompt"] == "built" and reply["warm_ms"] is not None
    assert rig.run({"type": "prepare", "id": "p2", "voice": voice})["prompt"] == "hit"


# ---------------------------------------------------------------- safety and validation


@pytest.mark.parametrize("reference", [
    "../outside.wav", "/etc/passwd", "library/../../outside.wav", "library/v1/reference.txt", "", None,
    "https://example.com/voice.wav"])
def test_references_outside_the_library_or_not_wav_are_refused(rig, tmp_path, reference):
    (tmp_path.parent / "outside.wav").write_bytes(b"RIFF")
    voice = {**rig.voice(), "reference": reference}
    reply = rig.run({"type": "speak", "id": "1", "text": "Hi.", "voice": voice})
    assert reply["type"] == "error"
    assert rig.base.prompts == [] and rig.base.clones == []


def test_symlinks_cannot_escape_the_library(rig, tmp_path):
    outside = tmp_path.parent / "secret.wav"
    outside.write_bytes(encode_wav(np.zeros(SR, np.float32) + 0.1, SR))
    link = tmp_path / "library" / "evil" / "reference.wav"
    link.parent.mkdir(parents=True)
    os.symlink(outside, link)
    voice = {**rig.voice(), "voice_id": "evil", "reference": "library/evil/reference.wav"}
    reply = rig.run({"type": "speak", "id": "1", "text": "Hi.", "voice": voice})
    assert reply["type"] == "error" and "outside" in reply["error"]


def test_a_missing_transcript_is_refused_rather_than_embedding_only_cloning(rig):
    voice = {**rig.voice(), "transcript": ""}
    reply = rig.run({"type": "speak", "id": "1", "text": "Hi.", "voice": voice})
    assert reply["type"] == "error" and "transcript" in reply["error"]
    assert rig.base.prompts == []


def test_a_missing_reference_file_is_an_error_not_a_fallback(rig):
    voice = rig.voice()
    (rig.root / voice["reference"]).unlink()
    reply = rig.run({"type": "speak", "id": "1", "text": "Hi.", "voice": voice})
    assert reply["type"] == "error" and "missing" in reply["error"]


def test_runaway_generation_hits_the_guard_and_is_regenerated_once(rig):
    voice = rig.voice()
    text = "A short sentence."
    rig.base.seconds = vw.length_cap(text) + 1      # generation runs into the cap
    reply = rig.run({"type": "speak", "id": "1", "text": text, "voice": voice})
    assert reply["retried"] is True and len(rig.base.clones) == 2
    assert rig.base.clones[0]["max_new_tokens"] == int(vw.length_cap(text) * vw.FRAME_RATE)


def test_unknown_commands_and_stop(rig):
    assert rig.run({"type": "bogus", "id": "1"})["type"] == "error"
    assert rig.service.handle({"type": "stop"}, lambda **m: None) is False
