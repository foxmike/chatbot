import os
from functools import lru_cache
from pathlib import Path

from pydantic import RedisDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_AVATAR_QWEN_MODEL = "Qwen3-TTS-12Hz-1.7B-VoiceDesign"   # creates voices (Voice Design)
DEFAULT_AVATAR_QWEN_CLONE_MODEL = "Qwen3-TTS-12Hz-1.7B-Base"    # speaks with saved voices (cloning)
DEFAULT_VOICE_LIBRARY_DIR = Path(__file__).resolve().parents[1] / "data" / "voices"
DEFAULT_AVATAR_QWEN_VOICE = (
    "A warm, natural young adult American woman with a relaxed, expressive conversational voice."
)


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://chatbot:chatbot@localhost:5432/chatbot"
    redis_url: RedisDsn | str = "redis://localhost:6379/0"
    jwt_secret_key: str = "dev-jwt-secret-key-change-me"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"
    environment: str = "development"

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    default_model: str = "google/gemma-4-31b-it"
    default_provider: str = "openrouter"

    llm_provider: str = "openrouter"  # openrouter | sglang | vllm | ollama
    sglang_base_url: str = "http://localhost:30000/v1"
    sglang_model: str = ""
    sglang_api_key: str = ""
    vllm_base_url: str = "http://localhost:8000/v1"
    vllm_model: str = ""
    vllm_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = ""
    ollama_api_key: str = ""
    ollama_num_ctx: int = int(os.getenv("OLLAMA_NUM_CTX", "8192"))

    task_class_routing_enabled: bool = True
    task_class_default_provider: str = "openrouter"
    task_class_default_model: str = "google/gemma-4-31b-it"
    task_class_casual_provider: str = "openrouter"
    task_class_casual_model: str = "google/gemma-4-31b-it"
    task_class_emotional_provider: str = "openrouter"
    task_class_emotional_model: str = "openrouter/deepseek/deepseek-v4-pro"
    task_class_summarize_provider: str = "openrouter"
    task_class_summarize_model: str = "openrouter/deepseek/deepseek-v3.2"
    task_class_extract_provider: str = "openrouter"
    task_class_extract_model: str = "openrouter/qwen/qwen3-coder-next"
    task_class_embed_provider: str = "openrouter"
    task_class_embed_model: str = "openrouter/z-ai/glm-4.7-flash"

    embedding_provider: str = "openrouter"
    embedding_model: str = "openai/text-embedding-3-small"

    # Avatar media settings use the same .env sources as the rest of the application.
    avatar_qwen_path: Path = Path("/home/mike/dev/tools/qwen")
    avatar_avtr_path: Path = Path("/home/mike/dev/tools/avtr-1")
    avatar_qwen_model: str = DEFAULT_AVATAR_QWEN_MODEL
    avatar_qwen_voice: str = DEFAULT_AVATAR_QWEN_VOICE
    avatar_stt_model: str = "base.en"
    avatar_stt_device: str = "cpu"
    avatar_stt_compute_type: str = "int8"
    avatar_stt_language: str = "en"
    avatar_endpoint_silence_ms: int = 550
    avatar_max_utterance_seconds: int = 30
    avatar_barge_in_enabled: bool = True
    avatar_sync_buffer_ms: int = 60
    avatar_portrait: str | None = None
    avatar_gpu_sampling: bool = False
    avatar_performance_enabled: bool = False
    avatar_performance_debug: bool = False
    avatar_worker_start_timeout: float = 120.0
    # Voice Library: Base speaks every saved voice; VoiceDesign is loaded only to design one
    # and released after this many idle seconds.
    avatar_qwen_clone_model: str = DEFAULT_AVATAR_QWEN_CLONE_MODEL
    avatar_design_idle_seconds: float = 90.0
    voice_library_dir: Path = DEFAULT_VOICE_LIBRARY_DIR
    voice_upload_max_bytes: int = 20 * 1024 * 1024
    voice_reference_min_seconds: float = 3.0
    voice_reference_max_seconds: float = 30.0
    voice_preview_ttl_seconds: int = 3600

    # Image Generation Configuration
    image_generation_provider: str = "openrouter"
    image_generation_model: str = "black-forest-labs/flux.2-pro"
    image_generation_api_key: str = ""
    image_generation_base_url: str = "https://openrouter.ai/api/v1"

    # Image Understanding Configuration
    image_understanding_provider: str = "openrouter"
    image_understanding_model: str = "openrouter/moonshotai/kimi-k2-0905"
    image_understanding_api_key: str = ""
    image_understanding_base_url: str = "https://openrouter.ai/api/v1"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    def task_class_routes(self) -> dict[str, tuple[str, str]]:
        return {
            "casual": (self.task_class_casual_provider, self.task_class_casual_model),
            "emotional": (self.task_class_emotional_provider, self.task_class_emotional_model),
            "summarize": (self.task_class_summarize_provider, self.task_class_summarize_model),
            "extract": (self.task_class_extract_provider, self.task_class_extract_model),
            "embed": (self.task_class_embed_provider, self.task_class_embed_model),
        }

    @model_validator(mode="after")
    def set_defaults(self) -> "Settings":
        provider = self.llm_provider.lower()
        self.default_provider = provider
        if provider == "sglang":
            self.default_model = self.sglang_model
        elif provider == "vllm":
            self.default_model = self.vllm_model
        elif provider == "ollama":
            self.default_model = self.ollama_model
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_embedding_config() -> dict[str, str]:
    settings = get_settings()
    return {
        "api_key": settings.openrouter_api_key,
        "base_url": getattr(settings, "embedding_base_url", settings.openrouter_base_url),
        "model": settings.embedding_model,
    }


def get_llm_config() -> dict[str, str]:
    settings = get_settings()
    provider = settings.llm_provider.lower()
    if provider == "sglang":
        return {
            "base_url": settings.sglang_base_url,
            "api_key": settings.sglang_api_key,
            "model": settings.sglang_model,
        }
    elif provider == "vllm":
        return {
            "base_url": settings.vllm_base_url,
            "api_key": settings.vllm_api_key,
            "model": settings.vllm_model,
        }
    elif provider == "ollama":
        return {
            "base_url": settings.ollama_base_url,
            "api_key": settings.ollama_api_key,
            "model": settings.ollama_model,
        }
    else:  # default to openrouter
        return {
            "base_url": settings.openrouter_base_url,
            "api_key": settings.openrouter_api_key,
            "model": settings.default_model,
        }
