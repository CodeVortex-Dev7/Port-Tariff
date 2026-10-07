from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM Settings (OpenAI-compatible endpoint for Google Gemini or others)
    llm_base_url: str = (
        "https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    llm_api_key: str = ""
    llm_model: str = "gemini-3.8-flash"
    llm_temperature: float = 0.0  # Zero temperature for deterministic extraction
    llm_timeout: float = 60.0
    llm_max_retries: int = 5  # SDK retries 429/5xx/timeouts with backoff
    llm_max_concurrency: int = 4  # parallel per-charge extraction calls

    # Application Settings
    app_env: str = "development"
    app_debug: bool = True
    port: int = 8000
    host: str = "0.0.0.0"
    # JSON list in the environment, e.g. CORS_ALLOW_ORIGINS=["https://app.example.com"]
    cors_allow_origins: list[str] = ["*"]

    # Logging Settings
    log_level: str = "INFO"
    log_format: str = "auto"  # "auto" -> console when app_debug else json
    log_prompt_preview_chars: int = 200

    # Ingestion / Cache Settings
    cache_parsed_markdown: bool = True
    category_aliases_path: str = "data/category_aliases.json"  # "" disables

    # Hugging Face TEI Embeddings Microservice
    embedding_api_base: str = "http://localhost:8080/v1"
    embedding_model_name: str = "BAAI/bge-m3"


@lru_cache
def get_settings() -> Settings:
    return Settings()
