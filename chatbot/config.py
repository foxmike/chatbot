from functools import lru_cache
import secrets

from pydantic import Field, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://chatbot:chatbot@localhost:5432/chatbot"
    redis_url: RedisDsn | str = "redis://localhost:6379/0"
    jwt_secret_key: str = Field(default_factory=lambda: secrets.token_hex(32))
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"
    environment: str = "development"

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    default_model: str = "google/gemma-4-31b-it"
    default_provider: str = "openrouter"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
