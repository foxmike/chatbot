"""Persistent Qwen3-TTS process for saved voices (Voice Library).

Runs in the Qwen environment, speaking JSON lines over stdio like `tts_worker.py`, whose
proven runaway guard (`length_cap`, `FRAME_RATE`) it reuses unchanged.

Models (separate checkpoints, one GPU, one thread):

* **Base** (`Qwen3-TTS-12Hz-1.7B-Base`) is loaded at start and stays warm. Every
  conversational sentence, every clone preview and every saved-voice preview is synthesized
  with `generate_voice_clone`, using a reusable prompt built once per voice by
  `create_voice_clone_prompt` from the voice's canonical reference recording + transcript.
* **VoiceDesign** (`Qwen3-TTS-12Hz-1.7B-VoiceDesign`) is loaded only when a voice-design
  preview is requested, and released again after `design_idle_seconds` without design work
  (or on `unload_design`), so both checkpoints are only resident while designing.

All model work happens on the main thread, in command order: GPU operations never overlap.
Prompts are cached in memory, keyed by (voice id, fingerprint); a changed reference or
transcript has a new fingerprint, so a stale prompt is never used. Nothing is persisted here:
after a restart prompts are rebuilt from the reference files.

References are only read from inside the voice library root given on the command line, and
are passed to Qwen as decoded (waveform, sample-rate) arrays, never as paths/URLs/base64
strings, so the model's own loader (which fetches URLs) is never involved.

Commands (stdin):
  {"type":"speak","id":..,"text":..,"voice":{voice_id,fingerprint,reference,transcript,language}}
  {"type":"prepare","id":..,"voice":{...},"warm":bool}
  {"type":"design","id":..,"text":..,"instruct":..,"language":..}
  {"type":"forget","id":..,"voice_id":..}
  {"type":"unload_design","id":..}
  {"type":"status","id":..}
  {"type":"stop"}
Replies (stdout): ready, audio, prepared, forgotten, design_unloaded, status, error.
"""
import base64
import contextlib
import importlib.util
import json
import os
import queue
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load_reference_worker():
    """The proven `tts_worker` module (runaway guard), loaded by path: it stays byte-identical."""
    spec = importlib.util.spec_from_file_location("chatbot_reference_tts_worker", _HERE / "tts_worker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_reference = _load_reference_worker()
FRAME_RATE = _reference.FRAME_RATE
length_cap = _reference.length_cap

MAX_CACHED_PROMPTS = 8
MAX_REFERENCE_BYTES = 64 * 1024 * 1024
DEFAULT_DESIGN_IDLE_SECONDS = 90.0


class VoiceWorkerError(RuntimeError):
    pass


def emit(**message):
    print(json.dumps(message, separators=(",", ":")), flush=True)


class VoiceTTSService:
    """Model lifecycle, prompt cache and synthesis; independent of stdio (unit-testable).

    `load_model(checkpoint)` returns a Qwen3TTSModel-like object; `read_audio(path)` returns
    (float32 mono waveform, sample rate); `release()` frees GPU memory after a model is dropped.
    """

    def __init__(self, base_checkpoint, design_checkpoint, voice_root, *, load_model, read_audio,
                 release=lambda: None, clock=time.monotonic, memory=lambda: None,
                 design_idle_seconds=DEFAULT_DESIGN_IDLE_SECONDS, max_prompts=MAX_CACHED_PROMPTS):
        self.base_checkpoint = str(base_checkpoint)
        self.design_checkpoint = str(design_checkpoint)
        self.voice_root = Path(os.path.realpath(voice_root))
        self._load_model = load_model
        self._read_audio = read_audio
        self._release = release
        self._clock = clock
        self._memory = memory
        self.design_idle_seconds = float(design_idle_seconds)
        self.max_prompts = max_prompts
        self.base = None
        self.design = None
        self.design_used_at = None
        self.prompts = OrderedDict()     # (voice_id, fingerprint) -> prompt items
        self.base_loads = 0
        self.design_loads = 0

    # ----------------------------------------------------------------- models

    def load_base(self):
        if self.base is None:
            try:
                self.base = self._load_model(self.base_checkpoint)
            except Exception as exc:
                raise VoiceWorkerError(f"Qwen3-TTS Base model failed to load ({self.base_checkpoint}): {exc}") from exc
            self.base_loads += 1
        return self.base

    def _design_model(self):
        if self.design is None:
            try:
                self.design = self._load_model(self.design_checkpoint)
            except Exception as exc:
                self._release()
                raise VoiceWorkerError(
                    f"Qwen3-TTS VoiceDesign model failed to load ({self.design_checkpoint}): {exc}") from exc
            self.design_loads += 1
        self.design_used_at = self._clock()
        return self.design

    def unload_design(self):
        """Release VoiceDesign; Base and its prompts are untouched. Returns whether it was loaded."""
        if self.design is None:
            return False
        self.design = None
        self.design_used_at = None
        self._release()
        return True

    def tick(self):
        """Release VoiceDesign after it has been idle; returns True when it was released."""
        if (self.design is not None and self.design_used_at is not None
                and self._clock() - self.design_used_at >= self.design_idle_seconds):
            return self.unload_design()
        return False

    # ----------------------------------------------------------------- voices

    def reference_path(self, path):
        """Resolve a reference inside the voice root; anything else is refused."""
        if not isinstance(path, str) or not path:
            raise VoiceWorkerError("voice reference path is missing")
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.voice_root / candidate
        resolved = Path(os.path.realpath(candidate))
        if resolved != self.voice_root and self.voice_root not in resolved.parents:
            raise VoiceWorkerError("voice reference is outside the voice library")
        if resolved.suffix.lower() != ".wav" or not resolved.is_file():
            raise VoiceWorkerError("voice reference recording is missing")
        if resolved.stat().st_size > MAX_REFERENCE_BYTES:
            raise VoiceWorkerError("voice reference recording is too large")
        return resolved

    @staticmethod
    def _voice_fields(voice):
        if not isinstance(voice, dict):
            raise VoiceWorkerError("no voice given")
        voice_id, fingerprint = voice.get("voice_id"), voice.get("fingerprint")
        transcript, language = voice.get("transcript"), voice.get("language") or "English"
        if not voice_id or not fingerprint:
            raise VoiceWorkerError("voice id and fingerprint are required")
        if not isinstance(transcript, str) or not transcript.strip():
            raise VoiceWorkerError("voice reference transcript is required")
        return str(voice_id), str(fingerprint), transcript, str(language)

    def prompt(self, voice):
        """(prompt items, "hit" | "built") for `voice`; builds and caches on first use."""
        voice_id, fingerprint, transcript, _ = self._voice_fields(voice)
        key = (voice_id, fingerprint)
        if key in self.prompts:
            self.prompts.move_to_end(key)
            return self.prompts[key], "hit"
        path = self.reference_path(voice.get("reference"))
        model = self.load_base()
        waveform, sr = self._read_audio(str(path))
        items = model.create_voice_clone_prompt(ref_audio=(waveform, int(sr)), ref_text=transcript,
                                                x_vector_only_mode=False)
        # The same voice with an older fingerprint is stale; drop it now rather than by LRU.
        for stale in [k for k in self.prompts if k[0] == voice_id]:
            del self.prompts[stale]
        self.prompts[key] = items
        while len(self.prompts) > self.max_prompts:
            self.prompts.popitem(last=False)
        return items, "built"

    def forget(self, voice_id):
        stale = [k for k in self.prompts if k[0] == str(voice_id)]
        for key in stale:
            del self.prompts[key]
        return len(stale)

    # ----------------------------------------------------------------- synthesis

    @staticmethod
    def _guarded(generate, text):
        """The proven runaway guard: cap the length, regenerate once if the cap is hit."""
        cap = length_cap(text)
        retried = False
        for _ in range(2):
            wavs, sr = generate(max_new_tokens=int(cap * FRAME_RATE))
            audio = wavs[0]
            if len(audio) / sr < cap - 0.2:
                break
            if retried:
                break
            retried = True
        return audio, sr, retried

    def speak(self, text, voice):
        if not isinstance(text, str) or not text.strip():
            raise VoiceWorkerError("text to speak is empty")
        items, cache = self.prompt(voice)
        _, _, _, language = self._voice_fields(voice)
        model = self.load_base()
        audio, sr, retried = self._guarded(
            lambda **limits: model.generate_voice_clone(text=text, language=language,
                                                        voice_clone_prompt=items, **limits), text)
        return audio, sr, retried, {"model": "base", "prompt": cache}

    def design_preview(self, text, instruct, language="English"):
        if not isinstance(text, str) or not text.strip():
            raise VoiceWorkerError("preview text is empty")
        if not isinstance(instruct, str) or not instruct.strip():
            raise VoiceWorkerError("voice description is empty")
        model = self._design_model()
        try:
            audio, sr, retried = self._guarded(
                lambda **limits: model.generate_voice_design(text=text, language=language or "English",
                                                             instruct=instruct, **limits), text)
        finally:
            self.design_used_at = self._clock()
        return audio, sr, retried, {"model": "design"}

    def status(self):
        return {"base_loaded": self.base is not None, "design_loaded": self.design is not None,
                "base_loads": self.base_loads, "design_loads": self.design_loads,
                "cached_prompts": [{"voice_id": v, "fingerprint": f} for v, f in self.prompts],
                "reserved_mib": self._memory()}

    # ----------------------------------------------------------------- protocol

    def handle(self, command, send):
        """Execute one command, replying through `send(**message)`. Returns False on stop."""
        kind = command.get("type")
        request_id = command.get("id")
        if kind == "stop":
            return False
        try:
            if kind in ("speak", "design"):
                start = self._clock()
                if kind == "speak":
                    audio, sr, retried, info = self.speak(command.get("text"), command.get("voice"))
                    info["voice_id"] = command["voice"]["voice_id"]
                    info["fingerprint"] = command["voice"]["fingerprint"]
                else:
                    audio, sr, retried, info = self.design_preview(
                        command.get("text"), command.get("instruct"), command.get("language"))
                pcm = _as_float32(audio)
                send(type="audio", id=request_id, sr=int(sr), audio=base64.b64encode(pcm.tobytes()).decode("ascii"),
                     duration=len(pcm) / sr, generation=self._clock() - start, retried=retried, **info)
            elif kind == "prepare":
                start = self._clock()
                _, cache = self.prompt(command.get("voice"))
                warm_ms = None
                if command.get("warm"):
                    warm = self._clock()
                    self.speak("Hello there.", command.get("voice"))
                    warm_ms = (self._clock() - warm) * 1000
                send(type="prepared", id=request_id, voice_id=command["voice"]["voice_id"],
                     fingerprint=command["voice"]["fingerprint"], prompt=cache,
                     prepare_ms=(self._clock() - start) * 1000, warm_ms=warm_ms)
            elif kind == "forget":
                send(type="forgotten", id=request_id, voice_id=command.get("voice_id"),
                     dropped=self.forget(command.get("voice_id")))
            elif kind == "unload_design":
                send(type="design_unloaded", id=request_id, released=self.unload_design(), reason="request")
            elif kind == "status":
                send(type="status", id=request_id, **self.status())
            else:
                send(type="error", id=request_id, error=f"unknown command {kind!r}")
        except Exception as exc:
            send(type="error", id=request_id, error=str(exc))
        return True


def _as_float32(audio):
    import numpy as np

    return np.ascontiguousarray(np.asarray(audio, dtype="<f4")).reshape(-1)


def _read_stdin(lines):
    for line in sys.stdin:
        lines.put(line)
    lines.put(None)


def main():
    base_checkpoint, design_checkpoint, voice_root = sys.argv[1], sys.argv[2], sys.argv[3]
    idle = float(sys.argv[4]) if len(sys.argv) > 4 else DEFAULT_DESIGN_IDLE_SECONDS

    import gc

    import numpy as np
    import soundfile as sf
    import torch

    def load_model(checkpoint):
        with contextlib.redirect_stdout(sys.stderr):
            from qwen_tts import Qwen3TTSModel
            return Qwen3TTSModel.from_pretrained(checkpoint, device_map="cuda:0", dtype=torch.bfloat16,
                                                 attn_implementation="sdpa")

    def read_audio(path):
        waveform, sr = sf.read(path, dtype="float32", always_2d=False)
        if waveform.ndim > 1:
            waveform = waveform.mean(axis=1)
        return np.ascontiguousarray(waveform, dtype=np.float32), int(sr)

    def release():
        gc.collect()
        torch.cuda.empty_cache()

    loading = time.monotonic()
    service = VoiceTTSService(base_checkpoint, design_checkpoint, voice_root, load_model=load_model,
                              read_audio=read_audio, release=release, design_idle_seconds=idle,
                              memory=lambda: torch.cuda.memory_reserved() / 1048576)
    service.load_base()
    emit(type="ready", checkpoint=base_checkpoint, design_checkpoint=design_checkpoint,
         backend="PyTorch SDPA", load_ms=(time.monotonic() - loading) * 1000, warmup_ms=0.0,
         torch_reserved_mib=torch.cuda.memory_reserved() / 1048576)

    lines = queue.Queue()
    threading.Thread(target=_read_stdin, args=(lines,), daemon=True).start()
    while True:
        try:
            line = lines.get(timeout=1.0)
        except queue.Empty:
            if service.tick():
                emit(type="design_unloaded", id=None, released=True, reason="idle",
                     torch_reserved_mib=torch.cuda.memory_reserved() / 1048576)
            continue
        if line is None:
            return
        try:
            command = json.loads(line)
        except json.JSONDecodeError:
            emit(type="error", id=None, error="invalid command")
            continue
        with contextlib.redirect_stdout(sys.stderr):
            keep_going = service.handle(command, lambda **m: _emit_real(m))
        if not keep_going:
            return


def _emit_real(message):
    # handle() runs with stdout redirected (Qwen prints); replies go to the real stdout.
    sys.__stdout__.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.__stdout__.flush()


if __name__ == "__main__":
    main()
