"""Настройки приложения. Все значения переопределяются переменными окружения с префиксом PSY_."""
from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class SsoProvider(BaseModel):
    client_id: str
    client_secret: str
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str
    scope: str = "openid email profile"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PSY_", env_file=".env", extra="ignore", env_nested_delimiter="__")

    env: str = "dev"
    database_url: str = "postgresql+asyncpg://psy:psy@localhost:5432/psytrainer"
    redis_url: str | None = "redis://localhost:6379/0"

    secret_key: str = "dev-secret-change-me-dev-secret-change-me"
    access_ttl_minutes: int = 15
    refresh_ttl_days: int = 30
    cookie_secure: bool = True  # в dev без HTTPS задайте PSY_COOKIE_SECURE=false
    public_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:5173"
    cors_origins: list[str] = ["http://localhost:5173"]

    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:30b"  # точный тег вашей модели Qwen 27B-30B
    judge_model: str | None = None  # отдельная модель для оценки и подсказок; по умолчанию как ollama_model
    ollama_num_ctx: int = 8192
    ollama_timeout: float = 180.0
    use_llm_classifier: bool = True  # False: разметка реплик только эвристикой (быстрее, грубее)

    storage_dir: str = "./storage"
    payment_webhook_secret: str = "dev-webhook-secret"
    plans: dict[str, int] = {"student_monthly": 30, "student_yearly": 365, "org_yearly": 365}  # план -> дней
    sso_providers: dict[str, SsoProvider] = {}

    rate_limit_auth_per_min: int = 10
    rate_limit_messages_per_min: int = 30
    rate_limit_default_per_min: int = 300

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    mail_from: str = "PsyTrainer <no-reply@psytrainer.example>"


@lru_cache
def get_settings() -> Settings:
    return Settings()
