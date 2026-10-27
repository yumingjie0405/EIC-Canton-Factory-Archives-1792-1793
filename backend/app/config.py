"""Application configuration."""

import json
from pathlib import Path
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings


class MissingDatabaseConfig(RuntimeError):
    """Raised when no database URL is configured.

    Deliberately a plain ``RuntimeError`` subclass raised at the point of
    use (Engine creation), never at import time: importing the settings
    module must not require a database.
    """


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database
    # DATABASE_URL HAS NO BUILT-IN DEFAULT: the previous fallback DSN embedded a
    # database password. A missing value must fail loudly instead of silently
    # connecting with a retired credential.
    database_url: str = ""
    database_echo: bool = False

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalise_database_url(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def require_database_url(self) -> str:
        """Return the configured database URL, or fail with an actionable error.

        The message never contains the URL itself, so it is safe to log.
        """
        url = (self.database_url or "").strip()
        if not url:
            raise MissingDatabaseConfig(
                "DATABASE_URL is not configured. Set it in backend/.env "
                "(git-ignored) or export it in the environment. There is no "
                "built-in default: the application never falls back to a "
                "retired credential."
            )
        return url

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Celery
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/0"
    celery_worker_pool: str = "solo"
    celery_worker_concurrency: int = 1

    # PaddleOCR Service
    paddle_ocr_url: str = "http://172.18.15.244:8888/layout-parsing"
    paddle_ocr_model_name: str = "PaddleOCR-VL-0.9B"
    paddle_ocr_timeout_seconds: int = 1800
    paddle_ocr_connect_timeout_seconds: int = 10
    paddle_ocr_write_timeout_seconds: int = 120
    paddle_ocr_retries: int = 2
    paddle_ocr_retry_backoff_seconds: float = 1.0
    paddle_ocr_trust_env: bool = False
    paddle_ocr_max_concurrency: int = 1
    paddle_ocr_max_file_mb: int = 200
    paddle_ocr_max_pixels: int = 2048 * 2048
    paddle_ocr_max_new_tokens: int = 4096

    # Historical PDF recognition budget; independent of stored preview image size.
    ocr_pdf_max_pixels: int = 8_388_608
    ocr_pdf_max_new_tokens: int = 8192
    ocr_image_max_pixels: int = 8_388_608
    ocr_image_max_new_tokens: int = 8192

    # GPT Service
    gpt_api_key: str = ""
    gpt_base_url: str = "https://api.siliconflow.cn/v1"
    gpt_model: str = "deepseek-ai/DeepSeek-V3"
    gpt_timeout: int = 300
    gpt_max_retries: int = 3  # Increased from 2 to 3 for better TLS fault tolerance
    gpt_retry_backoff_seconds: float = (
        2.0  # Increased from 1.0 to 2.0 for more stable retries
    )
    gpt_page_max_tokens: int = 8192
    gpt_page_enable_thinking: bool = False
    gpt_structured_output: bool = True
    correction_profiles_path: str = "config/correction_profiles.local.json"

    # RAG
    embedding_model: str = "BAAI/bge-m3"
    embedding_batch_size: int = 32
    rag_top_k: int = 8
    rag_answer_timeout_seconds: float = 150.0

    # Bounded research analysis calls; independent of OCR and RAG budgets.
    insights_request_timeout_seconds: float = 90.0

    # Knowledge-graph auto research (triggered by a successful Word import)
    #: Master switch. When off, no import/attach event may start an LLM run.
    #: A deployed instance that wants the feature must enable it explicitly
    #: (see ``.env.example``); groups additionally carry their own switch.
    kg_auto_extract_on_import: bool = False
    #: Per-run ceiling so an automatic run can never walk a whole book silently.
    kg_auto_extract_budget_blocks: int = 400
    #: Optional token ceiling for automatic runs (0 = no token limit).
    kg_auto_extract_budget_tokens: int = 0
    #: Automatic runs pause after this many consecutive block failures.
    kg_auto_extract_max_consecutive_failures: int = 3

    # Extraction budget safety (applies to *every* trigger, manual or automatic)
    #: Used when a caller does not pass ``budget_blocks``: no run may be unbounded.
    kg_extraction_default_budget_blocks: int = 200
    #: Hard ceiling for one run's block budget (a request above it is refused).
    kg_extraction_max_budget_blocks: int = 5000
    #: Hard ceiling for one run's token budget when a caller supplies one.
    kg_extraction_max_budget_tokens: int = 5000000
    #: Model names recorded for local/mock models. Runs using them are marked as
    #: mock-derived so their output is never presented as archival evidence.
    kg_mock_model_names: str = "mock-model,mock"
    #: Subset of the above that *reads the block text* and derives records by rules
    #: (a validation stub). Kept separate from the constant mock so the two kinds of
    #: test data can never be confused in the UI or in an audit.
    kg_text_derived_model_names: str = "mock-text-stub"

    @property
    def kg_text_derived_model_name_set(self) -> set:
        return {
            name.strip().lower()
            for name in (self.kg_text_derived_model_names or "").split(",")
            if name.strip()
        }

    @property
    def kg_mock_model_name_set(self) -> set:
        return {
            name.strip().lower()
            for name in (self.kg_mock_model_names or "").split(",")
            if name.strip()
        }

    # Storage paths
    storage_path: str = "../storage"
    upload_path: str = "../storage/uploads"
    pages_path: str = "../storage/pages"
    results_path: str = "../storage/results"
    exports_path: str = "../storage/exports"

    #: Hard ceiling for a single uploaded file. The browser has to hold the whole
    #: multipart body in memory, so unbounded uploads fail client-side with no
    #: server-side trace; exceeding this limit is reported explicitly (HTTP 413).
    max_upload_file_bytes: int = 512 * 1024 * 1024

    @property
    def max_upload_file_mb(self) -> int:
        """Ceiling expressed in whole megabytes, for user-facing messages."""
        return max(1, self.max_upload_file_bytes // (1024 * 1024))

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True

    # CORS
    cors_origins: str = '["http://localhost:3000","http://127.0.0.1:3000"]'

    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS origins from JSON string."""
        return json.loads(self.cors_origins)

    def get_storage_path(self) -> Path:
        """Get absolute storage path."""
        return Path(self.storage_path).resolve()

    def get_upload_path(self) -> Path:
        """Get absolute upload path."""
        return Path(self.upload_path).resolve()

    def get_pages_path(self) -> Path:
        """Get absolute pages path."""
        return Path(self.pages_path).resolve()

    def get_results_path(self) -> Path:
        """Get absolute results path."""
        return Path(self.results_path).resolve()

    def get_exports_path(self) -> Path:
        """Get absolute exports path."""
        return Path(self.exports_path).resolve()

    class Config:
        _backend_dir = Path(__file__).resolve().parent.parent
        # Only the real, git-ignored env file is read. ``.env.example`` is
        # documentation and must never act as a silent source of credentials.
        env_file = _backend_dir / ".env"
        env_file_encoding = "utf-8"


settings = Settings()
