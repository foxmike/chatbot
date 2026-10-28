"""Avatar media configuration, isolated from chatbot's global settings.

Every value is read from `AVATAR_*` environment variables (or constructed explicitly), so
nothing here touches `chatbot.config` or its validation. Defaults are the known-good
my_avatar configuration. Secrets never belong here: the media engine makes no network calls.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# Known-good voice. Do not edit without listening tests: the exact checkpoint and description
# are what the sampling parameters in tts_worker.py were tuned against.
DEFAULT_VOICE_MODEL = "Qwen3-TTS-12Hz-1.7B-VoiceDesign"
DEFAULT_VOICE = (
    "A warm, natural young adult American woman with a relaxed, expressive conversational voice."
)

REPLACED_CODE = 4001  # WebSocket close code: a newer browser window took over this session

_WORKER_DIR = Path(__file__).resolve().parent / "worker_scripts"


def _flag(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class MediaConfig:
    # Child-process environments (independent of chatbot's own Python).
    avtr_path: Path = Path("/home/mike/dev/tools/avtr-1")
    qwen_path: Path = Path("/home/mike/dev/tools/qwen")
    # Speech synthesis.
    voice_model: str = DEFAULT_VOICE_MODEL
    voice: str = DEFAULT_VOICE
    # Speech recognition (faster-whisper on CPU).
    stt_model: str = "base.en"
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"
    stt_language: str = "en"
    # Endpointing / barge-in.
    endpoint_silence: float = 0.55   # seconds; never below 0.35
    max_utterance_seconds: int = 30  # never below 5
    barge_in_enabled: bool = True
    # Browser synchronization buffer, announced to the page in the `config` message.
    sync_buffer_ms: int = 60
    # Optional portrait selected at start; otherwise call `set_portrait`.
    portrait: Path | None = None
    # nvidia-smi sampling for turn summaries (diagnostic only).
    gpu_sampling: bool = False
    worker_start_timeout: float = 120.0

    def __post_init__(self):
        object.__setattr__(self, "endpoint_silence", max(0.35, float(self.endpoint_silence)))
        object.__setattr__(self, "max_utterance_seconds", max(5, int(self.max_utterance_seconds)))

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> MediaConfig:
        env = os.environ if env is None else env
        defaults = cls()

        def get(name: str, default):
            value = env.get(f"AVATAR_{name}")
            return default if value is None or value == "" else value

        portrait = get("PORTRAIT", None)
        return cls(
            avtr_path=Path(get("AVTR_PATH", defaults.avtr_path)),
            qwen_path=Path(get("QWEN_PATH", defaults.qwen_path)),
            voice_model=get("QWEN_MODEL", defaults.voice_model),
            voice=get("QWEN_VOICE", defaults.voice),
            stt_model=get("STT_MODEL", defaults.stt_model),
            stt_device=get("STT_DEVICE", defaults.stt_device),
            stt_compute_type=get("STT_COMPUTE_TYPE", defaults.stt_compute_type),
            stt_language=get("STT_LANGUAGE", defaults.stt_language),
            endpoint_silence=int(get("ENDPOINT_SILENCE_MS", 550)) / 1000,
            max_utterance_seconds=int(get("MAX_UTTERANCE_SECONDS", 30)),
            barge_in_enabled=_flag(str(get("BARGE_IN_ENABLED", "true"))),
            sync_buffer_ms=int(get("SYNC_BUFFER_MS", defaults.sync_buffer_ms)),
            portrait=Path(portrait) if portrait else None,
            gpu_sampling=_flag(str(get("GPU_SAMPLING", "false"))),
        )

    @property
    def tts_mode(self) -> str:
        return "design" if "VoiceDesign" in self.voice_model else "custom"

    @property
    def avtr_python(self) -> Path:
        return self.avtr_path / ".pixi/envs/default/bin/python"

    @property
    def qwen_python(self) -> Path:
        return self.qwen_path / ".venv/bin/python"

    @property
    def qwen_checkpoint(self) -> Path:
        return self.qwen_path / "models" / self.voice_model

    @property
    def tts_script(self) -> Path:
        return _WORKER_DIR / "tts_worker.py"

    @property
    def avatar_script(self) -> Path:
        return _WORKER_DIR / "avatar_worker.py"
