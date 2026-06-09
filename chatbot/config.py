from functools import lru_cache

from pydantic import RedisDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # TTS (Text-to-Speech) Configuration
    tts_provider: str = "kokoro"
    tts_model: str = "hexgrad/kokoro-82m"
    tts_voice: str = "alloy"
    tts_speed: float = 1.0
    tts_api_key: str = ""
    tts_base_url: str = "https://openrouter.ai/api/v1"

    # STT (Speech-to-Text) Configuration
    stt_provider: str = "deepgram"
    stt_model: str = "deepgram/nova-2"
    stt_language: str = "en"
    stt_api_key: str = ""
    stt_base_url: str = "https://openrouter.ai/api/v1"

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
