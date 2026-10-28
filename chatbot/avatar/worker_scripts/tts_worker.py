"""Persistent Qwen3-TTS process with one waveform result per sentence."""
import base64
import contextlib
import json
import sys
import time

FRAME_RATE = 12.5  # Qwen3-TTS-12Hz codec frames per second of audio


def emit(**message):
    print(json.dumps(message, separators=(",", ":")), flush=True)


def length_cap(text):
    """Seconds of audio no sane rendering of `text` reaches (measured speech is ~0.054 s/char + 0.8 s).

    About 1.5x the typical length; only a runaway generation reaches it (the
    requested words are spoken, then no end-of-speech token is produced and the
    model rambles; measured ~1.5% of mid-sentence fragments, 0 of 224 complete
    sentences). Left alone it would stall the speech pipeline for 10-40 s. Sampling and voice parameters are untouched.
    """
    return 0.085 * len(text) + 1.6


def main():
    import numpy as np
    import torch

    loading = time.monotonic()
    with contextlib.redirect_stdout(sys.stderr):
        from qwen_tts import Qwen3TTSModel
        model = Qwen3TTSModel.from_pretrained(
            sys.argv[1], device_map="cuda:0", dtype=torch.bfloat16,
            attn_implementation="sdpa")

    def generate(text, voice, mode, **limits):
        if mode == "design":
            return model.generate_voice_design(text=text, language="English", instruct=voice, **limits)
        return model.generate_custom_voice(text=text, language="English", speaker=voice, **limits)

    # The first synthesis pays one-off CUDA/kernel setup; absorb it before "ready".
    warm = time.monotonic()
    with contextlib.redirect_stdout(sys.stderr):
        generate("Hello there.", sys.argv[2], "design" if "VoiceDesign" in sys.argv[1] else "custom")
    warm_ms = (time.monotonic() - warm) * 1000
    emit(type="ready", checkpoint=sys.argv[1], backend="PyTorch SDPA",
         load_ms=(time.monotonic() - loading) * 1000, warmup_ms=warm_ms,
         torch_reserved_mib=torch.cuda.memory_reserved() / 1048576)
    for line in sys.stdin:
        command = {}
        try:
            command = json.loads(line)
            if command.get("type") == "stop":
                return
            if command.get("type") != "speak":
                continue
            start = time.monotonic()
            text = command["text"]
            cap = length_cap(text)
            retried = False
            for _ in range(2):
                wavs, sr = generate(text, command["voice"], command["mode"],
                                    max_new_tokens=int(cap * FRAME_RATE))
                audio = np.asarray(wavs[0], dtype="<f4")
                if len(audio) / sr < cap - 0.2:
                    break
                if retried:
                    break
                retried = True  # hit the cap: runaway, regenerate once
            emit(type="audio", id=command["id"], sr=sr,
                 audio=base64.b64encode(memoryview(audio)).decode("ascii"),
                 duration=len(audio) / sr, generation=time.monotonic() - start, retried=retried)
        except Exception as exc:
            emit(type="error", id=command.get("id"), error=str(exc))


if __name__ == "__main__":
    main()
