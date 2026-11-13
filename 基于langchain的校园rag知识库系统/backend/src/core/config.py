import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = PROJECT_ROOT / "backend"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "校园 RAG 知识库系统"
    debug: bool = False
    api_prefix: str = "/api/v1"

    secret_key: str = "please-change-this-secret-in-env"
    access_token_expire_minutes: int = 480

    cors_origins: str = "http://localhost:5174,http://127.0.0.1:5174,http://localhost:5175,http://127.0.0.1:5175"

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3307
    mysql_user: str = "root"
    mysql_password: str = "123456"
    mysql_database: str = "rag_campus"

    redis_url: str = "redis://127.0.0.1:6379/4"

    admin_username: str = "admin"
    admin_display_name: str = "系统管理员"
    admin_initial_password: str = ""

    login_max_failures: int = 5
    login_window_seconds: int = 600
    login_lock_seconds: int = 600
    feedback_hourly_limit: int = 10

    llm_provider: str = "api"
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 2048
    llm_timeout_seconds: int = 120
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"

    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_device: str = "cpu"
    embedding_batch_size: int = 32
    reranker_model: str = "BAAI/bge-reranker-base"
    rerank_enabled: bool = True
    hf_endpoint: str = "https://hf-mirror.com"

    vector_top_k: int = 50
    bm25_top_k: int = 50
    rrf_k: int = 60
    rerank_top_n: int = 5
    context_max_chars: int = 6000
    chunk_size: int = 600
    chunk_overlap: int = 100
    history_max_turns: int = 5
    upload_max_mb: int = 20

    data_dir: str = ""
    storage_dir: str = ""
    indexes_dir: str = ""
    logs_dir: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def database_url(self) -> str:
        return (
            f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )

    @property
    def sync_database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir) if self.data_dir else PROJECT_ROOT / "data"

    @property
    def storage_path(self) -> Path:
        return Path(self.storage_dir) if self.storage_dir else PROJECT_ROOT / "storage"

    @property
    def indexes_path(self) -> Path:
        return Path(self.indexes_dir) if self.indexes_dir else PROJECT_ROOT / "indexes"

    @property
    def chroma_path(self) -> Path:
        return self.indexes_path / "chroma"

    @property
    def bm25_path(self) -> Path:
        return self.indexes_path / "bm25"

    @property
    def logs_path(self) -> Path:
        return Path(self.logs_dir) if self.logs_dir else PROJECT_ROOT / "logs"

    @property
    def hf_cache_path(self) -> Path:
        return PROJECT_ROOT / ".hf-cache"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

os.environ.setdefault("HF_HOME", str(settings.hf_cache_path))
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(settings.hf_cache_path))
os.environ.setdefault("HF_ENDPOINT", settings.hf_endpoint)
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
