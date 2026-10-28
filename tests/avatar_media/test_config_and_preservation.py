"""Configuration isolation and guards that the proven my_avatar behaviour was carried over intact."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from chatbot.avatar.config import DEFAULT_VOICE_MODEL, MediaConfig

AVATAR = Path(__file__).resolve().parents[2] / "chatbot" / "avatar"
REFERENCE = Path("/home/mike/dev/mike/my_avatar/python/my_avatar")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_voice_is_the_exact_known_good_configuration():
    config = MediaConfig()
    assert DEFAULT_VOICE_MODEL == "Qwen3-TTS-12Hz-1.7B-VoiceDesign" == config.voice_model
    assert config.voice == ("A warm, natural young adult American woman with a relaxed, expressive "
                            "conversational voice.")
    assert config.tts_mode == "design"
    assert config.qwen_checkpoint == Path("/home/mike/dev/tools/qwen/models/Qwen3-TTS-12Hz-1.7B-VoiceDesign")
    assert (config.stt_model, config.stt_device, config.stt_compute_type, config.stt_language) == (
        "base.en", "cpu", "int8", "en")
    assert (config.endpoint_silence, config.max_utterance_seconds, config.sync_buffer_ms,
            config.barge_in_enabled) == (0.55, 30, 60, True)


def test_environment_overrides_use_the_isolated_avatar_prefix():
    config = MediaConfig.from_env({
        "AVATAR_QWEN_VOICE": "A calm voice.", "AVATAR_ENDPOINT_SILENCE_MS": "100",
        "AVATAR_BARGE_IN_ENABLED": "false", "AVATAR_PORTRAIT": "/tmp/x.png",
        "STT_MODEL": "ignored", "QWEN_TTS_VOICE": "ignored", "OPENROUTER_API_KEY": "ignored"})
    assert config.voice == "A calm voice." and config.endpoint_silence == 0.35   # clamped like my_avatar
    assert config.barge_in_enabled is False and config.portrait == Path("/tmp/x.png")
    assert config.stt_model == "base.en" and config.voice_model == DEFAULT_VOICE_MODEL
    assert MediaConfig.from_env({}) == MediaConfig()


def test_custom_voice_checkpoints_select_custom_mode():
    assert MediaConfig(voice_model="Qwen3-TTS-12Hz-1.7B-CustomVoice", voice="Ryan").tts_mode == "custom"


def test_importing_the_package_is_cheap_and_media_config_ignores_chatbot_settings():
    code = ("import sys, chatbot.avatar; "
            "bad = [m for m in ('scipy','faster_whisper','cv2','torch') if m in sys.modules]; "
            "assert not bad, bad")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parents[2])
    assert result.returncode == 0, result.stderr


def test_media_engine_has_no_secrets_or_chatbot_dependencies():
    """The engine modules stay independent of settings, the DB, companions and any provider."""
    assert "chatbot.config" not in (AVATAR / "config.py").read_text().replace("chatbot.config`", "")
    forbidden = ("OPENROUTER", "api_key", "chatbot.db", "chatbot.companions", "sqlalchemy", "openai")
    engine = ("media.py", "session.py", "protocol.py", "stt.py", "workers.py", "metrics.py", "config.py",
              "speech_chunks.py")
    for path in (AVATAR / name for name in engine):
        text = path.read_text()
        assert not [word for word in forbidden if word in text], path


def test_tts_worker_runaway_guard_is_unchanged():
    tts = load(AVATAR / "worker_scripts" / "tts_worker.py", "tts_worker_under_test")
    assert tts.FRAME_RATE == 12.5
    assert tts.length_cap("x" * 100) == pytest.approx(0.085 * 100 + 1.6)


@pytest.mark.skipif(not REFERENCE.is_dir(), reason="my_avatar reference checkout not present")
@pytest.mark.parametrize("ported, original", [
    ("speech_chunks.py", "speech_chunks.py"),
    ("worker_scripts/tts_worker.py", "tts_worker.py"),
])
def test_proven_modules_are_byte_identical_to_the_reference(ported, original):
    """Chunker/planner/trimming and the Qwen worker (voice, sampling, runaway guard) must not drift."""
    assert (AVATAR / ported).read_bytes() == (REFERENCE / original).read_bytes()
