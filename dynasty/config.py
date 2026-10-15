"""Application configuration, read from environment variables.

There is no database. Everything configurable here is either a secret, a
cache setting, or a limit that protects upstream APIs.
"""

import logging
import os
import secrets
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        logger.warning("Invalid integer for %s; using %s", name, default)
        return default


def _bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def environment() -> str:
    """Return 'production', 'development' or 'testing'."""
    return (os.environ.get("APP_ENV") or os.environ.get("FLASK_ENV") or "development").lower()


def load_config(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    env = environment()
    is_production = env == "production"

    secret_key = os.environ.get("SECRET_KEY")
    if not secret_key:
        if is_production:
            raise RuntimeError("SECRET_KEY must be set when APP_ENV=production")
        # Random per process: sessions reset on restart, which is fine in development.
        secret_key = secrets.token_hex(32)
        logger.warning("SECRET_KEY is not set; using a temporary key for this process")

    config: Dict[str, Any] = {
        "ENV": env,
        "SECRET_KEY": secret_key,
        "DEBUG": _bool("FLASK_DEBUG", False) and not is_production,
        "TESTING": env == "testing",
        # Cookies
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": is_production,
        "PERMANENT_SESSION_LIFETIME": 7 * 24 * 3600,
        # Reverse proxy (Railway, Render, Fly, nginx): trust X-Forwarded-* headers.
        "TRUST_PROXY": _bool("TRUST_PROXY", is_production),
        # Sleeper
        "SLEEPER_BASE_URL": os.environ.get("SLEEPER_BASE_URL", "https://api.sleeper.app/v1"),
        "SLEEPER_TIMEOUT": _int("SLEEPER_TIMEOUT", 10),
        "LEAGUE_CACHE_SECONDS": _int("LEAGUE_CACHE_SECONDS", 300),
        # Trade values (FantasyCalc public API)
        "VALUES_BASE_URL": os.environ.get(
            "VALUES_BASE_URL", "https://api.fantasycalc.com/values/current"
        ),
        "VALUES_TIMEOUT": _int("VALUES_TIMEOUT", 15),
        "VALUES_CACHE_SECONDS": _int("VALUES_CACHE_SECONDS", 6 * 3600),
        "CACHE_DIR": os.environ.get("CACHE_DIR", os.path.join(os.getcwd(), ".cache")),
        # OpenAI (optional, used only to write explanations)
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
        "OPENAI_MODEL": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        "OPENAI_TIMEOUT": _int("OPENAI_TIMEOUT", 20),
        # Per-client limits (in memory, per process)
        "LLM_CALLS_PER_HOUR": _int("LLM_CALLS_PER_HOUR", 20),
        "REQUESTS_PER_MINUTE": _int("REQUESTS_PER_MINUTE", 60),
        "MAX_SUGGESTIONS": _int("MAX_SUGGESTIONS", 6),
    }
    if overrides:
        config.update(overrides)
    return config
