import re
from collections.abc import Mapping
from django.core.exceptions import ImproperlyConfigured
from urllib.parse import urlsplit

APP_ENV_DEBUG = "debug"
APP_ENV_PROD = "prod"
APP_ENVS = (APP_ENV_DEBUG, APP_ENV_PROD)

TELEGRAM_TOKEN_VARS = {
    APP_ENV_DEBUG: "TELEGRAM_BOT_TOKEN_DEBUG",
    APP_ENV_PROD: "TELEGRAM_BOT_TOKEN_PROD",
}

_TELEGRAM_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]+$")


def parse_app_env(value: str | None) -> str:
    if value not in APP_ENVS:
        raise ImproperlyConfigured(f"Invalid APP_ENV: expected one of {APP_ENVS}, got {value!r}")
    return value


def select_telegram_token(app_env: str, environ: Mapping[str, str]) -> str:
    var_name = TELEGRAM_TOKEN_VARS[app_env]
    token = environ.get(var_name, "").strip()
    if not token:
        raise ImproperlyConfigured(f"{var_name} is required when APP_ENV={app_env}")
    if not _TELEGRAM_TOKEN_RE.match(token):
        raise ImproperlyConfigured(f"{var_name} does not look like a Telegram bot token ('<bot_id>:<secret>')")

    other_var = next(name for env, name in TELEGRAM_TOKEN_VARS.items() if env != app_env)
    if environ.get(other_var, "").strip() == token:
        raise ImproperlyConfigured(f"{var_name} and {other_var} must be different bots")
    return token


def normalize_base_url(name: str, value: str, log: list[str], *, root_only: bool = False) -> str:
    expected = "an absolute http(s) URL" + (" without a path" if root_only else "")
    parts = urlsplit(value)
    if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.query or parts.fragment
            or (root_only and parts.path not in ("", "/"))
    ):
        raise ImproperlyConfigured(f"Invalid {name}: expected {expected}, got {value!r}")

    if value.endswith("/"):
        return value
    log.append(f"Added trailing slash to {name}.")
    return value + "/"


def require_non_empty(name: str, value: str) -> str:
    if not value.strip():
        raise ImproperlyConfigured(f"{name} must not be empty")
    return value
