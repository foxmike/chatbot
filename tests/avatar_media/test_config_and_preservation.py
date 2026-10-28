"""Settings integration and guards for the proven my_avatar media behaviour."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from chatbot.avatar.config import DEFAULT_VOICE_MODEL, MediaConfig
from chatbot.config import Settings, get_settings

AVATAR = Path(__file__).resolve().parents[2] / "chatbot" / "avatar"
REFERENCE = Path("/home/mike/dev/mike/my_avatar/python/my_avatar")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_voice_is_the_exact_known_good_configuration():
    config = MediaConfig.from_settings(Settings(_env_file=None))
    assert DEFAULT_VOICE_MODEL == "Qwen3-TTS-12Hz-1.7B-VoiceDesign" == config.voice_model
    assert config.voice == ("A warm, natural young adult American woman with a relaxed, expressive "
                            "conversational voice.")
    assert config.tts_mode == "design"
    assert config.qwen_checkpoint == Path("/home/mike/dev/tools/qwen/models/Qwen3-TTS-12Hz-1.7B-VoiceDesign")
    assert (config.stt_model, config.stt_device, config.stt_compute_type, config.stt_language) == (
        "base.en", "cpu", "int8", "en")
    assert (config.endpoint_silence, config.max_utterance_seconds, config.sync_buffer_ms,
            config.barge_in_enabled) == (0.55, 30, 60, True)


def test_settings_overrides_are_converted_and_keep_existing_bounds():
    settings = Settings(
        _env_file=None,
        avatar_endpoint_silence_ms=100, avatar_max_utterance_seconds=1,
        avatar_barge_in_enabled=False, avatar_portrait="/tmp/x.png",
        avatar_qwen_path="/tmp/qwen", avatar_avtr_path="/tmp/avtr",
        avatar_gpu_sampling=True, avatar_worker_start_timeout=90,
    )
    config = MediaConfig.from_settings(settings)
    assert (config.endpoint_silence, config.max_utterance_seconds) == (0.35, 5)
    assert config.barge_in_enabled is False and config.portrait == Path("/tmp/x.png")
    assert config.qwen_path == Path("/tmp/qwen") and config.avtr_path == Path("/tmp/avtr")
    assert config.gpu_sampling is True and config.worker_start_timeout == 90
    assert config.voice_model == DEFAULT_VOICE_MODEL


def test_normal_runtime_host_reads_dotenv_through_settings(tmp_path, monkeypatch):
    from chatbot.avatar.runtime import AvatarRuntimeHost

    for name in Settings.model_fields:
        if name.startswith("avatar_"):
            monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        'AVATAR_STT_MODEL=tiny.en\n'
        'AVATAR_STT_DEVICE=cpu\n'
        'AVATAR_STT_COMPUTE_TYPE=int8\n'
        'AVATAR_STT_LANGUAGE=fr\n'
        'AVATAR_ENDPOINT_SILENCE_MS=650\n'
        'AVATAR_MAX_UTTERANCE_SECONDS=40\n'
        'AVATAR_SYNC_BUFFER_MS=80\n'
        'AVATAR_BARGE_IN_ENABLED=false\n'
        'AVATAR_QWEN_VOICE="A calm test description."\n'
    )
    get_settings.cache_clear()
    try:
        host = AvatarRuntimeHost()
        config = host.engine.config
        assert config.stt_model == "tiny.en" and config.stt_language == "fr"
        assert (config.endpoint_silence, config.max_utterance_seconds) == (0.65, 40)
        assert config.sync_buffer_ms == 80 and config.barge_in_enabled is False
        assert config.voice == "A calm test description."
        assert config.voice_model == "Qwen3-TTS-12Hz-1.7B-VoiceDesign"
        assert host.engine.avatar.process is None and host.engine.tts.process is None
    finally:
        get_settings.cache_clear()


def test_custom_voice_checkpoints_select_custom_mode():
    assert MediaConfig(voice_model="Qwen3-TTS-12Hz-1.7B-CustomVoice", voice="Ryan").tts_mode == "custom"


def test_importing_the_package_does_not_load_models():
    code = ("import sys, chatbot.avatar; "
            "bad = [m for m in ('scipy','faster_whisper','cv2','torch') if m in sys.modules]; "
            "assert not bad, bad")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parents[2])
    assert result.returncode == 0, result.stderr


def test_media_engine_has_no_secrets_or_chatbot_dependencies():
    """Only the Settings adapter is coupled to application configuration, never secrets."""
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
