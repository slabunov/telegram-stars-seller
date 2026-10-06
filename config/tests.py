"""Проверки конфигурации."""
import json
import os
import pytest
import subprocess
import sys
from django.core.exceptions import ImproperlyConfigured
from pathlib import Path
from urllib.parse import urljoin

from config.env import (
    normalize_base_url, parse_app_env, require_non_empty,
    select_telegram_token
)

DEBUG_TOKEN = "111:debug-secret"
PROD_TOKEN = "222:prod-secret"
BOTH = {"TELEGRAM_BOT_TOKEN_DEBUG": DEBUG_TOKEN, "TELEGRAM_BOT_TOKEN_PROD": PROD_TOKEN}


# APP_ENV

@pytest.mark.parametrize("raw", ["debug", "prod"])
def test_app_env_accepts_known_values(raw):
    assert parse_app_env(raw) == raw


@pytest.mark.parametrize("raw", [None, "", "test", "production", "Prod", "dev", "staging"])
def test_app_env_rejects_everything_else(raw):
    with pytest.raises(ImproperlyConfigured, match="Invalid APP_ENV"):
        _ = parse_app_env(raw)


# Telegram token

def test_debug_env_selects_debug_token():
    assert select_telegram_token("debug", BOTH) == DEBUG_TOKEN


def test_prod_env_selects_prod_token():
    assert select_telegram_token("prod", BOTH) == PROD_TOKEN


def test_prod_never_falls_back_to_debug_token():
    with pytest.raises(ImproperlyConfigured, match="TELEGRAM_BOT_TOKEN_PROD is required when APP_ENV=prod"):
        _ = select_telegram_token("prod", {"TELEGRAM_BOT_TOKEN_DEBUG": DEBUG_TOKEN})


def test_debug_never_falls_back_to_prod_token():
    with pytest.raises(ImproperlyConfigured, match="TELEGRAM_BOT_TOKEN_DEBUG is required when APP_ENV=debug"):
        _ = select_telegram_token("debug", {"TELEGRAM_BOT_TOKEN_PROD": PROD_TOKEN})


def test_same_token_for_both_bots_is_rejected():
    with pytest.raises(ImproperlyConfigured, match="must be different bots"):
        _ = select_telegram_token("prod",
                                  {"TELEGRAM_BOT_TOKEN_DEBUG": PROD_TOKEN, "TELEGRAM_BOT_TOKEN_PROD": PROD_TOKEN})


def test_malformed_token_error_does_not_leak_the_value():
    with pytest.raises(ImproperlyConfigured) as exc_info:
        _ = select_telegram_token("debug", {"TELEGRAM_BOT_TOKEN_DEBUG": "super secret value"})
    assert "super secret value" not in str(exc_info.value)


# URLs

def test_url_without_slash_is_normalized_and_logged():
    log: list[str] = []
    assert normalize_base_url("FRAGMENT_API_URL", "https://api.fragment-api.com/v1",
                              log) == "https://api.fragment-api.com/v1/"
    assert log == ["Added trailing slash to FRAGMENT_API_URL."]


def test_url_with_slash_is_unchanged_and_not_logged():
    log: list[str] = []
    assert normalize_base_url("FRAGMENT_API_URL", "https://api.fragment-api.com/v1/",
                              log) == "https://api.fragment-api.com/v1/"
    assert log == []


@pytest.mark.parametrize("raw", ["https://api.paypear.ru/v1", "https://api.paypear.ru/v1/"])
def test_path_is_preserved_in_final_api_url(raw):
    assert urljoin(normalize_base_url("PAYPEAR_API_URL", raw, []), "payment/") == "https://api.paypear.ru/v1/payment/"


@pytest.mark.parametrize("raw", ["https://example.com", "https://example.com/"])
def test_site_domain_is_accepted_with_or_without_slash(raw):
    assert normalize_base_url("SITE_DOMAIN", raw, [], root_only=True) == "https://example.com/"
    assert normalize_base_url("SITE_DOMAIN", "http://localhost:8000", [], root_only=True) == "http://localhost:8000/"


@pytest.mark.parametrize("value", [
    "",
    "example.com",
    "ftp://example.com/",
    "https:///",
    "https://example.com/app",
    "https://example.com/?a=1",
])
def test_bad_site_domain_is_rejected(value):
    with pytest.raises(ImproperlyConfigured, match="Invalid SITE_DOMAIN: expected an absolute http"):
        _ = normalize_base_url("SITE_DOMAIN", value, [], root_only=True)


@pytest.mark.parametrize("value", ["ftp://api.paypear.ru/v1", "https:///v1", "api.paypear.ru/v1"])
def test_bad_api_url_is_rejected(value):
    with pytest.raises(ImproperlyConfigured, match="Invalid PAYPEAR_API_URL"):
        _ = normalize_base_url("PAYPEAR_API_URL", value, [])


def test_required_value_must_not_be_blank():
    with pytest.raises(ImproperlyConfigured, match="PAYPEAR_SECRET must not be empty"):
        _ = require_non_empty("PAYPEAR_SECRET", "  ")


# settings.py и стартовые логи целиком

_BASE_ENV = {
    "DJANGO_SECRET_KEY": "x", "FRAGMENT_WEBHOOK_SECRET": "s", "SITE_DOMAIN": "https://example.com/",
    "TELEGRAM_ADMIN_CHAT_ID": "1", "TELEGRAM_ADMIN_BROADCAST_TOPIC_ID": "2", "TELEGRAM_ADMIN_ORDERS_TOPIC_ID": "3",
    "TELEGRAM_CHANNEL_ID": "4", "TELEGRAM_CHANNEL_LINK": "https://t.me/x",
    "FRAGMENT_API_URL": "https://api.fragment-api.com/v1/", "FRAGMENT_CURRENCY": "TON",
    "PLATEGA_API_URL": "https://app.platega.io/", "PLATEGA_MERCHANT_ID": "m", "PLATEGA_SECRET": "s",
    "PAYPEAR_API_URL": "https://api.paypear.ru/v1/", "PAYPEAR_SHOP_ID": "1", "PAYPEAR_SECRET": "s",
    **BOTH,
}

_PROBE = """
import json, environ, django
environ.Env.read_env = lambda *a, **k: None
import config.settings as s
django.setup()
print(json.dumps({
    "APP_ENV": s.APP_ENV, "IS_DEBUG": s.IS_DEBUG, "IS_PROD": s.IS_PROD, "DEBUG": s.DEBUG,
    "root_level": s.LOGGING["root"]["level"], "token": s.TELEGRAM_BOT_TOKEN, "SECRET_KEY": s.SECRET_KEY,
    "telegram": [s.TELEGRAM_ADMIN_CHAT_ID, s.TELEGRAM_ADMIN_BROADCAST_TOPIC_ID, s.TELEGRAM_ADMIN_ORDERS_TOPIC_ID,
                 s.TELEGRAM_CHANNEL_ID, s.TELEGRAM_CHANNEL_LINK],
    "urls": [s.SITE_DOMAIN, s.FRAGMENT_API_URL, s.PLATEGA_API_URL, s.PAYPEAR_API_URL],
}))
"""


def _run_settings(**overrides: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.pathsep.join(sys.path),
        "DJANGO_SETTINGS_MODULE": "config.settings", "PYTHONDONTWRITEBYTECODE": "1", **_BASE_ENV, **overrides,
    }
    return subprocess.run(
        [sys.executable, "-c", _PROBE], env=env, cwd=Path(__file__).resolve().parent.parent,
        capture_output=True, text=True, timeout=60
    )


def _startup_messages(stderr: str) -> list[str]:
    return [line.split(" ", 4)[4] for line in stderr.splitlines() if line.startswith("INFO ") and " apps " in line]


def test_debug_env_settings_and_startup_log():
    result = _run_settings(APP_ENV="debug", FRAGMENT_API_URL="https://api.fragment-api.com/v1")
    assert result.returncode == 0, result.stderr

    values = json.loads(result.stdout)
    assert {k: values[k] for k in ("APP_ENV", "IS_DEBUG", "IS_PROD", "DEBUG", "root_level", "token", "SECRET_KEY")} == {
        "APP_ENV": "debug", "IS_DEBUG": True, "IS_PROD": False, "DEBUG": True,
        "root_level": "DEBUG", "token": DEBUG_TOKEN, "SECRET_KEY": "x",
    }
    assert values["telegram"] == [1, 2, 3, 4, "https://t.me/x"]
    assert values["urls"][1] == "https://api.fragment-api.com/v1/"
    assert _startup_messages(result.stderr) == [
        "Application environment: debug",
        "Django debug mode enabled.",
        # "Telegram bot configuration: debug",
        "Added trailing slash to FRAGMENT_API_URL.",
    ]
    assert DEBUG_TOKEN not in result.stderr


def test_prod_env_settings_and_startup_log():
    result = _run_settings(APP_ENV="prod", SITE_DOMAIN="https://example.com")
    assert result.returncode == 0, result.stderr

    values = json.loads(result.stdout)
    assert {k: values[k] for k in ("APP_ENV", "IS_DEBUG", "IS_PROD", "DEBUG", "root_level", "token")} == {
        "APP_ENV": "prod", "IS_DEBUG": False, "IS_PROD": True, "DEBUG": False, "root_level": "INFO",
        "token": PROD_TOKEN,
    }
    assert values["urls"] == [
        "https://example.com/", "https://api.fragment-api.com/v1/", "https://app.platega.io/",
        "https://api.paypear.ru/v1/"
    ]
    assert _startup_messages(result.stderr) == [
        "Application environment: prod",
        # "Telegram bot configuration: prod",
        "Added trailing slash to SITE_DOMAIN.",
    ]
    assert PROD_TOKEN not in result.stderr


@pytest.mark.parametrize("overrides, error", [
    ({"APP_ENV": "production"}, "Invalid APP_ENV"),
    ({"APP_ENV": "debug", "SITE_DOMAIN": "example.com"}, "Invalid SITE_DOMAIN"),
])
def test_settings_fail_at_import_with_clear_error(overrides, error):
    result = _run_settings(**overrides)
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr and error in result.stderr
    assert DEBUG_TOKEN not in result.stderr and PROD_TOKEN not in result.stderr
