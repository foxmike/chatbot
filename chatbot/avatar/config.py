"""Lightweight media-worker configuration, constructed from chatbot's Settings.

Application configuration and .env loading belong to chatbot.config. This dataclass
contains only media values and derived worker paths; it never reads the environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from chatbot.config import (
    DEFAULT_AVATAR_QWEN_MODEL as DEFAULT_VOICE_MODEL,
    DEFAULT_AVATAR_QWEN_VOICE as DEFAULT_VOICE,
    Settings,
)

REPLACED_CODE = 4001  # WebSocket close code: a newer browser window took over this session

_WORKER_DIR = Path(__file__).resolve().parent / "worker_scripts"


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
    def from_settings(cls, settings: Settings) -> MediaConfig:
        return cls(
            avtr_path=settings.avatar_avtr_path,
            qwen_path=settings.avatar_qwen_path,
            voice_model=settings.avatar_qwen_model,
            voice=settings.avatar_qwen_voice,
            stt_model=settings.avatar_stt_model,
            stt_device=settings.avatar_stt_device,
            stt_compute_type=settings.avatar_stt_compute_type,
            stt_language=settings.avatar_stt_language,
            endpoint_silence=settings.avatar_endpoint_silence_ms / 1000,
            max_utterance_seconds=settings.avatar_max_utterance_seconds,
            barge_in_enabled=settings.avatar_barge_in_enabled,
            sync_buffer_ms=settings.avatar_sync_buffer_ms,
            portrait=Path(settings.avatar_portrait) if settings.avatar_portrait else None,
            gpu_sampling=settings.avatar_gpu_sampling,
            worker_start_timeout=settings.avatar_worker_start_timeout,
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
