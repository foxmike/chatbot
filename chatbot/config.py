from functools import lru_cache

from pydantic import RedisDsn
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
    tts_provider: str = "openai"
    tts_model: str = "tts-1"
    tts_voice: str = "alloy"
    tts_speed: float = 1.0
    tts_api_key: str = ""
    tts_base_url: str = "https://api.openai.com/v1"

    # STT (Speech-to-Text) Configuration
    stt_provider: str = "openai"
    stt_model: str = "whisper-1"
    stt_language: str = "en"
    stt_api_key: str = ""
    stt_base_url: str = "https://api.openai.com/v1"

    # Image Generation Configuration
    image_generation_provider: str = "diffusion"
    image_generation_model: str = "stable-diffusion-v1-5"
    image_generation_api_key: str = ""
    image_generation_base_url: str = "https://api.openai.com/v1"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
