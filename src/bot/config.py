"""Configuration module using Pydantic settings."""
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    # Telegram Bot
    BOT_TOKEN: str = Field(..., description="Telegram Bot Token")
    # NoDecode: a bare env value isn't valid JSON for a list field, and
    # pydantic-settings would otherwise try to json.loads() it and blow up
    # before the comma-splitting validator below ever runs.
    ADMIN_IDS: Annotated[list[int], NoDecode] = Field(
        ..., description="Comma-separated admin Telegram user IDs"
    )

    # Update delivery
    BOT_MODE: str = Field(
        default="polling",
        description="How updates are delivered: 'polling' or 'webhook'"
    )
    WEBHOOK_URL: str | None = Field(
        default=None,
        description="Publicly reachable HTTPS URL registered with Telegram for webhook mode"
    )
    WEBHOOK_HOST: str = Field(
        default="0.0.0.0",
        description="Host the webhook server binds to"
    )
    WEBHOOK_PORT: int = Field(
        default=8081,
        description="Port the webhook server listens on"
    )
    WEBHOOK_PATH: str = Field(
        default="/webhook",
        description="URL path the webhook server listens on"
    )
    WEBHOOK_SECRET: str | None = Field(
        default=None,
        description="Secret token Telegram sends back on every webhook request for verification"
    )

    # Ollama
    OLLAMA_HOST: str = Field(
        default="http://localhost:11434",
        description="Ollama API host URL"
    )
    OLLAMA_API_KEY: str | None = Field(
        default=None,
        description="Bearer token for LiteLLM or other authenticated Ollama-compatible endpoints"
    )
    OLLAMA_TIMEOUT: int = Field(
        default=60,
        description="Ollama API timeout in seconds"
    )

    # Database
    DATABASE_URL: str = Field(
        default="sqlite:///data/bot.db",
        description="Database connection URL"
    )

    # Application
    TEST_MODE: bool = Field(
        default=False,
        description="Test mode (admin only)"
    )
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Logging level"
    )
    MAX_CONTEXT_TOKENS: int = Field(
        default=3000,
        description=(
            "Approximate token budget for the conversation history. The count "
            "is an estimate biased to overcount, since Ollama exposes no "
            "tokenizer; keep it below the model's num_ctx."
        )
    )
    OLLAMA_KEEP_ALIVE: str = Field(
        default="10m",
        description="How long Ollama keeps the model loaded between requests"
    )
    OLLAMA_TEMPERATURE: float | None = Field(
        default=None,
        description="Sampling temperature; None leaves the model default"
    )
    OLLAMA_NUM_CTX: int | None = Field(
        default=None,
        description="Context window size in tokens; None leaves the model default"
    )
    DEFAULT_MODEL: str = Field(
        default="llama2",
        description="Default Ollama model"
    )

    # Rate Limiting
    RATE_LIMIT_MESSAGES: int = Field(
        default=10,
        description="Maximum messages per rate limit window"
    )
    RATE_LIMIT_WINDOW: int = Field(
        default=60,
        description="Rate limit window in seconds"
    )

    MAX_CONCURRENT_UPDATES: int = Field(
        default=32,
        description="Updates processed in parallel; 1 makes one slow user block everyone"
    )

    # Health Check
    HEALTH_CHECK_ENABLED: bool = Field(
        default=True,
        description="Enable health check endpoint"
    )
    HEALTH_CHECK_PORT: int = Field(
        default=8080,
        description="Health check server port"
    )

    # Optional: Monitoring
    SENTRY_DSN: str | None = Field(
        default=None,
        description="Sentry DSN for error tracking"
    )
    PROMETHEUS_ENABLED: bool = Field(
        default=False,
        description="Enable Prometheus metrics"
    )
    PROMETHEUS_PORT: int = Field(
        default=9090,
        description="Prometheus metrics port"
    )

    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def parse_admin_ids(cls, v: str | int | list[int]) -> list[int]:
        """Accept a comma-separated env value or an already-parsed list."""
        if isinstance(v, str):
            return [int(part.strip()) for part in v.split(",") if part.strip()]
        if isinstance(v, int):
            return [v]
        return v

    @field_validator("BOT_MODE")
    @classmethod
    def validate_bot_mode(cls, v: str) -> str:
        """Validate update delivery mode."""
        v = v.lower()
        if v not in ("polling", "webhook"):
            raise ValueError("BOT_MODE must be 'polling' or 'webhook'")
        return v

    @model_validator(mode="after")
    def validate_webhook_config(self) -> "Settings":
        """Webhook mode needs a URL to register with Telegram and a secret to verify requests."""
        if self.BOT_MODE == "webhook":
            if not self.WEBHOOK_URL:
                raise ValueError("WEBHOOK_URL is required when BOT_MODE=webhook")
            if not self.WEBHOOK_SECRET:
                raise ValueError("WEBHOOK_SECRET is required when BOT_MODE=webhook")
        return self

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v = v.upper()
        if v not in valid_levels:
            raise ValueError(f"Invalid log level. Must be one of: {valid_levels}")
        return v

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """Ensure SQLite database directory exists."""
        if v.startswith("sqlite:///"):
            db_path = Path(v.replace("sqlite:///", ""))
            db_path.parent.mkdir(parents=True, exist_ok=True)
        return v

    @property
    def is_postgres(self) -> bool:
        """Check if using PostgreSQL."""
        return self.DATABASE_URL.startswith("postgresql")

    @property
    def is_sqlite(self) -> bool:
        """Check if using SQLite."""
        return self.DATABASE_URL.startswith("sqlite")


# Create global settings instance
settings = Settings()
