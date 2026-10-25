from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Security
    secret_key: str = "dev-secret-change-me"
    access_token_expire_minutes: int = 60
    algorithm: str = "HS256"
    cors_origins: str = "*"  # comma-separated list in production

    # Storage
    database_url: str = "postgresql+asyncpg://docusense:docusense@postgres:5432/docusense"
    sync_database_url: str = ""  # derived from database_url when empty
    redis_url: str = "redis://redis:6379/0"  # empty string disables the semantic cache
    upload_dir: str = "/data/uploads"

    # Ingestion: "celery" (docker-compose, scalable) or "inline" (free hosting, no worker)
    ingest_mode: str = "celery"

    # Vector store
    qdrant_url: str = "http://qdrant:6333"  # ":memory:" for tests
    qdrant_api_key: str = ""  # required for Qdrant Cloud
    qdrant_collection: str = "docusense_chunks"

    # Inference: "ollama" (self-hosted) or "openai" (any OpenAI-compatible API, e.g. Groq)
    llm_provider: str = "ollama"
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "llama3.1:8b"
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    llm_model: str = "llama-3.1-8b-instant"

    # Embeddings (fastembed / ONNX — no PyTorch, small memory footprint)
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str = "/srv/models"

    # Agent tuning
    max_agent_iterations: int = 2
    retrieval_top_k: int = 8
    rerank_keep_k: int = 5
    max_sub_questions: int = 4
    semantic_cache_threshold: float = 0.95


@lru_cache
def get_settings() -> Settings:
    return Settings()
