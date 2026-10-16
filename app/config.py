"""Application configuration classes for Development, Testing, and Production.

Strictly enforces PostgreSQL as the single source of truth (RULE-07) and
reads secrets from environment variables (RULE-21). Never falls back to SQLite.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

from dotenv import load_dotenv

load_dotenv()

PLACEHOLDER_SECRET_KEYS = {
    "",
    "replace-with-a-64-char-hex-secret-key-generated-locally",
    "changeme",
    "secret",
    "dev",
}


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def validate_postgresql_uri(uri: str | None, env_var_name: str = "DATABASE_URL") -> str:
    """Validate that the configured database URI is a PostgreSQL connection string.

    Strictly forbids SQLite or in-memory fallbacks per Project Rule #7.
    """
    if not uri or not uri.strip():
        raise RuntimeError(
            f"Missing required PostgreSQL configuration: '{env_var_name}' is not set. "
            "Configure a valid PostgreSQL connection URL in your .env file or environment."
        )

    cleaned = uri.strip()
    lower = cleaned.lower()

    if lower.startswith("sqlite"):
        raise RuntimeError(
            f"Invalid database configuration in '{env_var_name}': SQLite is prohibited by Project Rule #7. "
            "PostgreSQL is the required single source of truth."
        )

    if lower.startswith("postgres://"):
        cleaned = "postgresql+psycopg2://" + cleaned[len("postgres://") :]
        lower = cleaned.lower()
    elif lower.startswith("postgresql+psycopg://"):
        cleaned = "postgresql+psycopg2://" + cleaned[len("postgresql+psycopg://") :]
        lower = cleaned.lower()
    elif lower.startswith("postgresql://"):
        cleaned = "postgresql+psycopg2://" + cleaned[len("postgresql://") :]
        lower = cleaned.lower()

    if not lower.startswith("postgresql+psycopg2://"):
        raise RuntimeError(
            f"Invalid database URI scheme in '{env_var_name}'. "
            "Expected a PostgreSQL URI starting with 'postgresql://' or 'postgresql+psycopg2://'."
        )

    return cleaned


class BaseConfig:
    """Shared base configuration across environments."""

    APP_NAME: str = os.getenv("APP_NAME", "Sanchay AI")
    FLASK_ENV: str = os.getenv("FLASK_ENV", "development")
    DEBUG: bool = False
    TESTING: bool = False

    SECRET_KEY: str = os.getenv("SECRET_KEY", "")

    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False
    SQLALCHEMY_ECHO: bool = _parse_bool(os.getenv("SQLALCHEMY_ECHO"), default=False)
    SQLALCHEMY_ENGINE_OPTIONS: dict[str, Any] = {
        "pool_pre_ping": True,
    }

    WTF_CSRF_ENABLED: bool = _parse_bool(os.getenv("WTF_CSRF_ENABLED"), default=True)
    WTF_CSRF_TIME_LIMIT: int = int(os.getenv("WTF_CSRF_TIME_LIMIT", "3600"))
    WTF_CSRF_HEADERS: list[str] = ["X-CSRFToken", "X-CSRF-Token"]

    SESSION_COOKIE_NAME: str = os.getenv("SESSION_COOKIE_NAME", "sanchay_session")
    SESSION_COOKIE_HTTPONLY: bool = _parse_bool(
        os.getenv("SESSION_COOKIE_HTTPONLY"), default=True
    )
    SESSION_COOKIE_SAMESITE: str = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_COOKIE_SECURE: bool = _parse_bool(
        os.getenv("SESSION_COOKIE_SECURE"), default=False
    )
    PERMANENT_SESSION_LIFETIME: timedelta = timedelta(
        minutes=int(os.getenv("PERMANENT_SESSION_LIFETIME_MINUTES", "480"))
    )

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE_PATH: str = os.getenv("LOG_FILE_PATH", "logs/sanchay_ai.log")

    # Business timezone for deterministic expiry-date boundary evaluation (default IST: Asia/Kolkata)
    APP_TIMEZONE: str = os.getenv("APP_TIMEZONE", "Asia/Kolkata")

    # Phase 5 Deterministic Rule-Based Stock Analysis parameters (non-AI)
    ANALYSIS_RULE_VERSION: str = os.getenv(
        "ANALYSIS_RULE_VERSION", "v1.0.0-phase5-deterministic"
    )
    ANALYSIS_LOOKBACK_DAYS: int = int(os.getenv("ANALYSIS_LOOKBACK_DAYS", "14"))
    ANALYSIS_MIN_HISTORY_DAYS: int = int(os.getenv("ANALYSIS_MIN_HISTORY_DAYS", "7"))
    ANALYSIS_MIN_ACTIVE_SALE_DAYS: int = int(
        os.getenv("ANALYSIS_MIN_ACTIVE_SALE_DAYS", "2")
    )
    ANALYSIS_DEFAULT_SAFETY_STOCK_DAYS: int = int(
        os.getenv("ANALYSIS_DEFAULT_SAFETY_STOCK_DAYS", "2")
    )
    ANALYSIS_SURPLUS_COVERAGE_DAYS: int = int(
        os.getenv("ANALYSIS_SURPLUS_COVERAGE_DAYS", "30")
    )
    ANALYSIS_STALE_AFTER_SECONDS: int = int(
        os.getenv("ANALYSIS_STALE_AFTER_SECONDS", "300")
    )

    # Phase 8 AI flag must remain False throughout Phases 1-7
    AI_ENABLED: bool = False


class DevelopmentConfig(BaseConfig):
    """Local development configuration."""

    FLASK_ENV: str = "development"
    DEBUG: bool = _parse_bool(os.getenv("FLASK_DEBUG"), default=True)
    SECRET_KEY: str = (
        os.getenv("SECRET_KEY") or "dev-only-ephemeral-secret-key-do-not-use-in-prod-9f8e7d6c5b4a"
    )
    SQLALCHEMY_DATABASE_URI: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:postgres@localhost:5432/sanchay_ai_dev",
    )


class TestingConfig(BaseConfig):
    """Automated pytest configuration targeting an isolated PostgreSQL test database."""

    FLASK_ENV: str = "testing"
    TESTING: bool = True
    DEBUG: bool = False
    SECRET_KEY: str = "test-suite-deterministic-secret-key-0123456789abcdef"
    WTF_CSRF_ENABLED: bool = True
    SESSION_COOKIE_SECURE: bool = False
    SQLALCHEMY_DATABASE_URI: str = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+psycopg2://postgres:postgres@localhost:5432/sanchay_ai_test",
    )


class ProductionConfig(BaseConfig):
    """Production configuration with strict secret and HTTPS cookie enforcement."""

    FLASK_ENV: str = "production"
    DEBUG: bool = False
    TESTING: bool = False
    SESSION_COOKIE_SECURE: bool = _parse_bool(
        os.getenv("SESSION_COOKIE_SECURE"), default=True
    )
    SQLALCHEMY_DATABASE_URI: str = os.getenv("DATABASE_URL", "")


CONFIG_BY_NAME: dict[str, type[BaseConfig]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def validate_app_config(app_config: dict[str, Any]) -> None:
    """Validate critical configuration settings at application startup."""
    env_name = str(app_config.get("FLASK_ENV", "development")).lower()
    secret_key = str(app_config.get("SECRET_KEY", "")).strip()

    if env_name == "production":
        if secret_key in PLACEHOLDER_SECRET_KEYS or len(secret_key) < 32:
            raise RuntimeError(
                "Production configuration error: A strong SECRET_KEY (>= 32 characters, "
                "not a placeholder) must be set via environment variables."
            )
    else:
        if not secret_key:
            raise RuntimeError("Configuration error: SECRET_KEY must not be empty.")

    db_uri = app_config.get("SQLALCHEMY_DATABASE_URI")
    env_var_label = "TEST_DATABASE_URL" if app_config.get("TESTING") else "DATABASE_URL"
    validated_uri = validate_postgresql_uri(db_uri, env_var_name=env_var_label)

    if app_config.get("TESTING") and "test" not in validated_uri.lower():
        raise RuntimeError(
            "Safety guard triggered: TestingConfig requires a PostgreSQL database name "
            "containing 'test' (e.g., 'sanchay_ai_test') to prevent accidental data loss "
            "on development or production databases."
        )

    app_config["SQLALCHEMY_DATABASE_URI"] = validated_uri
