"""Маппинг статусов, суммы в минорных единицах и создание платежа PayHot."""
import asyncio
import httpx
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from core.domain.enums import TransactionStatus
from core.domain.schemas.payment import PaymentRequest
from core.integrations.payhot.client import PayHotClient, to_amount_minor
from core.integrations.payhot.enums import PayHotStatus
from core.integrations.payhot.errors import PayHotAPIError
from core.integrations.payhot.provider import PayHotPaymentProvider
from core.services.payment_providers import InvalidPaymentMethodError

_transform = PayHotStatus.transform_into_internal_status_or_keep_original


def test_status_mapping_covers_every_payhot_status():
    assert _transform("succeeded") == TransactionStatus.PROCESSING
    assert _transform("failed") == TransactionStatus.CANCELLED
    assert _transform("cancelled") == TransactionStatus.CANCELLED
    assert _transform("refunded") == TransactionStatus.CHARGEBACKED
    assert _transform("partially_refunded") == TransactionStatus.CHARGEBACKED
    for status in ("requires_payment_method", "requires_action", "processing", "authorized", "manual_reconciliation"):
        assert _transform(status) == TransactionStatus.PENDING
    assert _transform("WAT") == "WAT"


@pytest.mark.parametrize("amount, minor", [
    (Decimal("100"), "10000"), (Decimal("349.9"), "34990"), (Decimal("0.005"), "1"), (Decimal("12.344"), "1234"),
])
def test_amount_is_sent_in_kopecks(amount, minor):
    assert to_amount_minor(amount) == minor


@pytest.fixture
def prod(settings):
    settings.IS_DEBUG = False
    settings.PAYHOT_API_URL = "https://app.pay.hot/api/v2/"
    settings.PAYHOT_API_KEY = "key"


def test_create_payment_sends_order_and_reads_checkout(prod):
    http = MagicMock()
    http.post.return_value = httpx.Response(201, json={
        "payment": {"id": "p", "status": "requires_payment_method", "merchant_order_reference": "x",
                    "amount_minor": "34990", "currency": "RUB"},
        "checkout": {"url": "https://checkout.pay.hot/#token", "expires_at": "2099-01-01T00:00:00Z"},
    })

    dto = asyncio.run(PayHotClient(http).create_payment("sbp", Decimal("349.9"), "RUB"))

    url, = http.post.call_args.args
    kwargs = http.post.call_args.kwargs
    assert url == "https://app.pay.hot/api/v2/payments"
    assert kwargs["json"] == {
        "merchant_order_reference": str(dto.transaction_id), "amount_minor": "34990", "currency": "RUB",
        "payment_method": "sbp",
    }
    assert kwargs["headers"] == {"Authorization": "Bearer key", "Idempotency-Key": str(dto.transaction_id)}
    assert isinstance(dto.transaction_id, UUID)
    assert dto.pay_url == "https://checkout.pay.hot/#token"
    assert dto.provider_payment_id == "p"
    assert dto.price == Decimal("349.9")
    assert dto.expires_in == "00:30:00"


def test_create_payment_raises_with_payhot_error_details(prod):
    http = MagicMock()
    http.post.return_value = httpx.Response(422, json={"error": {"code": "invalid_amount", "message": "bad"}})

    with pytest.raises(PayHotAPIError, match="invalid_amount: bad"):
        _ = asyncio.run(PayHotClient(http).create_payment("sbp", Decimal("1"), "RUB"))


def _request() -> PaymentRequest:
    return PaymentRequest(
        amount=Decimal("100.50"), currency="RUB", description="d", buyer_username="buyer",
        payload={
            "user_id": 1, "message_id": 2, "price": 100.5, "stars_count": 50, "target_username": "buyer",
            "payment_api": "PayHot", "pay_url": "", "promo_id": None, "promo_name": "", "promo_discount": None,
        },
    )


def test_provider_passes_method_code_and_amount():
    client = MagicMock(create_payment=AsyncMock())
    _ = asyncio.run(PayHotPaymentProvider(client).create_payment("crypto", _request()))
    client.create_payment.assert_awaited_once_with("crypto", Decimal("100.50"), "RUB")


@pytest.mark.parametrize("method", ["", "SBP", "2", "bank_card"])
def test_provider_rejects_unknown_method(method):
    with pytest.raises(InvalidPaymentMethodError):
        _ = asyncio.run(PayHotPaymentProvider(MagicMock()).create_payment(method, _request()))
