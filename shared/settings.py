from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # Core
    env: str = Field(default="dev", alias="ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Postgres
    database_url: str = Field(..., alias="DATABASE_URL")

    # Redis / Celery
    redis_url: str = Field(..., alias="REDIS_URL")

    # HTTP fetch for ingest
    http_timeout_s: int = Field(default=45, alias="HTTP_TIMEOUT_S")

    # OpenAI
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL")
    openai_embed_model: str = Field(default="text-embedding-3-small", alias="OPENAI_EMBED_MODEL")
    embed_dim: int = Field(default=1536, alias="EMBED_DIM")
    openai_timeout_s: int = Field(default=60, alias="OPENAI_TIMEOUT_S")
    openai_max_retries: int = Field(default=3, alias="OPENAI_MAX_RETRIES")

    # Chunking / retrieval
    chunk_size_chars: int = Field(default=1200, alias="CHUNK_SIZE_CHARS")
    chunk_overlap_chars: int = Field(default=200, alias="CHUNK_OVERLAP_CHARS")
    retrieval_k: int = Field(default=6, alias="RETRIEVAL_K")
    max_context_chars: int = Field(default=14000, alias="MAX_CONTEXT_CHARS")

    # Admin
    admin_token: str = Field(default="change-me", alias="ADMIN_TOKEN")

    # Bot
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    api_base_url: str = Field(default="http://api:8000", alias="API_BASE_URL")
    api_timeout_s: int = Field(default=45, alias="API_TIMEOUT_S")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
