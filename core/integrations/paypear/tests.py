"""Проверки маппинга статусов, проброса контекста заказа через metadata и создания платежа PayPear."""

import asyncio
import httpx
from unittest.mock import MagicMock

from core.domain.enums import TransactionStatus
from core.domain.schemas.payment import PaymentPayloadDict
from core.integrations.paypear.client import PayPearClient, extract_payment_object
from core.integrations.paypear.enums import PayPearStatus
from core.integrations.paypear.schemas import build_paypear_metadata, parse_paypear_metadata

_transform = PayPearStatus.transform_into_internal_status_or_keep_original


def test_status_mapping_covers_every_paypear_status():
    assert _transform("CONFIRMED") == TransactionStatus.PROCESSING
    assert _transform("CANCELED") == TransactionStatus.CANCELLED
    assert _transform("EXPIRED") == TransactionStatus.CANCELLED
    assert _transform("REFUNDED") == TransactionStatus.CHARGEBACKED
    assert _transform("NEW") == TransactionStatus.PENDING
    assert _transform("PROCESS") == TransactionStatus.PENDING
    # неизвестный статус возвращается как есть
    assert _transform("WAT") == "WAT"


def _payload(**overrides: object) -> PaymentPayloadDict:
    base: PaymentPayloadDict = {
        "user_id": 111,
        "message_id": 222,
        "price": 349.9,
        "stars_count": 250,
        "target_username": "someone",
        "payment_api": "PayPear",
        "pay_url": "",
        "promo_id": None,
        "promo_name": "",
        "promo_discount": None,
    }
    base.update(overrides)  # pyright: ignore[reportArgumentType]
    return base


def test_metadata_roundtrip_without_promo():
    payload = _payload()
    restored = parse_paypear_metadata(build_paypear_metadata(payload))
    assert restored == payload


def test_metadata_roundtrip_with_promo():
    payload = _payload(promo_id=7, promo_name="SUMMER", promo_discount="5.00")
    restored = parse_paypear_metadata(build_paypear_metadata(payload))
    assert restored == payload


def test_metadata_bad_input_returns_none():
    assert parse_paypear_metadata(None) is None
    assert parse_paypear_metadata({}) is None
    assert parse_paypear_metadata({"user_id": "not-a-number"}) is None
    assert parse_paypear_metadata({"only": "some", "keys": "here"}) is None


def test_extract_payment_object_accepts_both_wrapper_keys():
    obj = {"id": "x", "status": "NEW"}
    assert extract_payment_object({"success": True, "result": obj}) is obj  # pyright: ignore[reportArgumentType]
    assert extract_payment_object({"success": True, "response": obj}) is obj  # pyright: ignore[reportArgumentType]
    assert extract_payment_object({"success": False}) is None


def test_webhook_url_is_built_from_site_domain(settings):
    settings.SITE_DOMAIN = "https://shop.example/"
    assert PayPearClient.build_webhook_url() == "https://shop.example/core/webhooks/paypear/"


def test_create_payment_always_sends_webhook_url(settings):
    settings.SITE_DOMAIN = "https://shop.example/"
    settings.PAYPEAR_API_URL = "https://api.paypear.ru/v1/"
    settings.IS_DEBUG = False
    http = MagicMock()
    http.post.return_value = httpx.Response(200, json={"success": True, "result": {
        "status": "NEW", "amount": {"value": "349.90", "currency": "RUB"},
        "confirmation": {"type": "redirect", "confirmation_url": "https://pay.example/x"},
    }})

    dto = asyncio.run(PayPearClient(http).create_payment("sbp", 349.9, "RUB", "desc", _payload()))

    sent = http.post.call_args.kwargs["json"]
    assert http.post.call_args.args[0] == "https://api.paypear.ru/v1/payment/"
    assert sent["webhook_url"] == "https://shop.example/core/webhooks/paypear/"
    assert sent["payment_method_data"] == {"type": "sbp"}
    assert sent["order_id"] == str(dto.transaction_id)
    assert dto.pay_url == "https://pay.example/x"
