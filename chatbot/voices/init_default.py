"""Explicit initialization of the built-in default voice.

The avatar used to speak every sentence with Qwen3-TTS VoiceDesign from the configured
description (AVATAR_QWEN_VOICE). Saved voices instead speak through the Base model from a
canonical reference recording. This command keeps the configured description as the
built-in default voice: VoiceDesign renders ONE reference recording from it, which is
stored as the default voice's identity and used for every later sentence.

The new reference is a fresh VoiceDesign rendering of the same description. It is not, and
is not claimed to be, bit-for-bit identical to any earlier per-sentence output.

    python -m chatbot.voices.init_default            # create it (refuses if it exists)
    python -m chatbot.voices.init_default --replace  # re-render and replace the reference

Admins can do the same through the running server: POST /voices/default/initialize.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from chatbot.db.models import Voice
from chatbot.voices.audio import encode_wav, wav_duration
from chatbot.voices.service import DEFAULT_VOICE_ID, MODEL_FAMILY
from chatbot.voices.storage import VoiceStorage

DEFAULT_VOICE_NAME = "Default (built-in)"
REFERENCE_TEXT = ("Hi, it's really good to hear from you. I was just thinking about how quiet the "
                  "evening has been, and honestly, I'm glad you reached out.")
MIN_REFERENCE_SECONDS = 3.0


class InitializationError(RuntimeError):
    pass


async def initialize_default_voice(engine, db, storage: VoiceStorage, *, description: str,
                                   design_model: str, clone_model: str, replace: bool = False,
                                   initialized_by: str = "init_default") -> dict:
    """Render the default voice's reference with VoiceDesign and save it as the built-in voice."""
    existing = await db.get(Voice, DEFAULT_VOICE_ID)
    if existing is not None and not replace:
        raise InitializationError("The built-in default voice already exists (use --replace to re-render it)")
    result = await engine.design_preview(REFERENCE_TEXT, description, "English")
    wav = encode_wav(result["audio"], result["sr"])
    if wav_duration(wav) < MIN_REFERENCE_SECONDS:
        raise InitializationError("VoiceDesign produced a reference shorter than 3 seconds; try again")
    stored = storage.store_reference(DEFAULT_VOICE_ID, wav, replace=True)
    now = datetime.now(timezone.utc).isoformat()
    metadata = {"initialized_by": initialized_by, "initialized_at": now,
                "note": ("Reference rendered once by VoiceDesign from the configured default description. "
                         "A new rendering, not bit-identical to earlier per-sentence VoiceDesign output.")}
    fields = dict(name=DEFAULT_VOICE_NAME, method="designed", description=description,
                  reference_path=stored.reference_path, reference_sha256=stored.sha256,
                  reference_duration=stored.duration, reference_sample_rate=stored.sample_rate,
                  reference_transcript=REFERENCE_TEXT, language="English", model_family=MODEL_FAMILY,
                  creation_model=design_model, synthesis_model=clone_model, status="ready",
                  is_builtin=True, extra_metadata=metadata)
    if existing is None:
        db.add(Voice(voice_id=DEFAULT_VOICE_ID, user_id=None, **fields))
    else:
        for key, value in fields.items():
            setattr(existing, key, value)
    await db.commit()
    try:
        await engine.forget_voice(DEFAULT_VOICE_ID)   # a replaced reference must not reuse an old prompt
    except Exception:  # noqa: BLE001 - the new fingerprint already prevents reuse
        pass
    try:
        await engine.unload_design()
    except Exception:  # noqa: BLE001 - the worker releases it after its idle timeout anyway
        pass
    return {"voice_id": DEFAULT_VOICE_ID, "replaced": existing is not None, "duration": stored.duration,
            "sha256": stored.sha256, "transcript": REFERENCE_TEXT}


class _TTSOnlyEngine:
    """The media engine's Qwen worker alone (no AVTR, no STT), for the command line."""

    def __init__(self, config):
        from chatbot.avatar.media import AvatarMediaEngine

        self.engine = AvatarMediaEngine(config)

    async def __aenter__(self):
        return self.engine

    async def __aexit__(self, *exc):
        await self.engine.tts.stop()
        for task in self.engine._pumps:  # noqa: SLF001 - CLI teardown of its own engine
            task.cancel()
        await asyncio.gather(*self.engine._pumps, return_exceptions=True)  # noqa: SLF001


async def _main(replace: bool) -> int:
    from chatbot.avatar.config import MediaConfig
    from chatbot.config import get_settings
    from chatbot.db import async_session_factory

    settings = get_settings()
    config = MediaConfig.from_settings(settings)
    storage = VoiceStorage(config.voice_root)
    print(f"Rendering the default voice with {config.voice_model}:\n  {config.voice}")
    async with _TTSOnlyEngine(config) as engine, async_session_factory() as db:
        try:
            result = await initialize_default_voice(
                engine, db, storage, description=config.voice, design_model=config.voice_model,
                clone_model=config.clone_model, replace=replace)
        except InitializationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    print(f"{'Replaced' if result['replaced'] else 'Created'} built-in default voice {result['voice_id']} "
          f"({result['duration']:.1f}s reference, sha256 {result['sha256'][:12]}…)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--replace", action="store_true", help="re-render and replace an existing default voice")
    args = parser.parse_args(argv)
    return asyncio.run(_main(args.replace))


if __name__ == "__main__":
    raise SystemExit(main())
