"""Вебхуки платёжных провайдеров и Fragment callback URL."""
import asyncio
import json
import pytest
from django.test import RequestFactory
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import core.views as views
from core.domain.enums import TransactionStatus
from core.domain.schemas.payment import PaymentPayloadDict
from core.integrations.fragment.client import FragmentClient
from core.integrations.paypear.client import PayPearClient
from core.integrations.paypear.schemas import build_paypear_metadata
from core.integrations.platega.client import PlategaClient
from core.integrations.webhook_utils import ServicesNames

PAYPEAR_IP = "158.160.85.101"


def _payload(payment_api: str) -> PaymentPayloadDict:
    return {
        "user_id": 111, "message_id": 222, "price": 349.9, "stars_count": 250, "target_username": "someone",
        "payment_api": payment_api, "pay_url": "https://pay", "promo_id": None, "promo_name": "",
        "promo_discount": None,
    }


class _Lock:
    released = False

    async def release(self) -> None:
        self.released = True


@pytest.fixture
def pipeline(monkeypatch, settings):
    settings.PAYPEAR_SHOP_ID = "42"
    settings.PAYPEAR_WEBHOOK_IPS = [PAYPEAR_IP]
    settings.PLATEGA_MERCHANT_ID = "merchant"
    settings.PLATEGA_SECRET = "platega-secret"

    mocks = MagicMock()
    mocks.lock = _Lock()
    mocks.save = AsyncMock(return_value=True)
    mocks.acquire = AsyncMock(return_value=mocks.lock)
    mocks.task = MagicMock()

    mocks.paypear = MagicMock(get_verified_payment=AsyncMock(side_effect=lambda order_id, obj: obj))
    monkeypatch.setattr(views, "get_container", lambda: MagicMock(get=AsyncMock(return_value=mocks.paypear)))
    monkeypatch.setattr(views, "async_save_status_by_key", mocks.save)
    monkeypatch.setattr(views, "async_acquire_lock", mocks.acquire)
    monkeypatch.setattr(views, "update_transaction_status_task", mocks.task)
    return mocks


def _paypear_request(transaction_id: UUID, status: str = "CONFIRMED", shop_id: int = 42):
    body = {
        "type": "notification", "event": "payment.succeeded", "signature": "",
        "object": {
            "id": str(uuid4()), "shop_id": shop_id, "order_id": str(transaction_id), "status": status, "paid": True,
            "amount": {"value": "349.90", "currency": "RUB"}, "created_at": "2026-01-01T00:00:00Z",
            "metadata": build_paypear_metadata(_payload("PayPear")),
        },
    }
    return RequestFactory().post(
        "/core/webhooks/paypear/", data=json.dumps(body), content_type="application/json", REMOTE_ADDR=PAYPEAR_IP
    )


def test_paypear_confirmed_webhook_moves_transaction_to_processing(pipeline):
    transaction_id = uuid4()

    response = asyncio.run(views.paypear_webhook(_paypear_request(transaction_id)))

    assert response.status_code == 200
    pipeline.save.assert_awaited_once_with(ServicesNames.PAYPEAR, transaction_id, str(TransactionStatus.PROCESSING))
    pipeline.task.apply_async.assert_called_once_with(
        args=(str(transaction_id), _payload("PayPear"), ""), kwargs={"started_at": None}
    )
    assert pipeline.lock.released


def test_paypear_concurrent_duplicate_is_rejected_for_retry(pipeline):
    pipeline.acquire.return_value = None

    response = asyncio.run(views.paypear_webhook(_paypear_request(uuid4())))

    assert response.status_code == 429
    pipeline.save.assert_not_awaited()
    pipeline.task.apply_async.assert_not_called()


def test_paypear_webhook_for_another_shop_is_forbidden(pipeline):
    response = asyncio.run(views.paypear_webhook(_paypear_request(uuid4(), shop_id=7)))
    assert response.status_code == 403
    pipeline.task.apply_async.assert_not_called()


def test_platega_confirmed_webhook_still_works(pipeline):
    transaction_id = uuid4()
    body = {
        "id": str(transaction_id), "amount": 349.9, "currency": "RUB", "status": "CONFIRMED", "paymentMethod": 2,
        "payload": json.dumps(_payload("Platega")),
    }
    request = RequestFactory().post(
        "/core/webhooks/platega/", data=json.dumps(body), content_type="application/json",
        headers={"X-MerchantId": "merchant", "X-Secret": "platega-secret"},
    )

    response = asyncio.run(views.payment_webhook(request))

    assert response.status_code == 200
    pipeline.save.assert_awaited_once_with(ServicesNames.PLATEGA, transaction_id, str(TransactionStatus.PROCESSING))
    pipeline.task.apply_async.assert_called_once_with(
        args=(str(transaction_id), _payload("Platega"), "2"), kwargs={"started_at": None}
    )


def test_fake_providers_follow_app_env_debug(settings):
    settings.IS_DEBUG = True
    http = MagicMock()

    platega = asyncio.run(PlategaClient(http).create_payment(2, 100.0, "RUB", "d", "1", "u", payload="{}"))
    paypear = asyncio.run(PayPearClient(http).create_payment("sbp", 100.0, "RUB", "d", _payload("PayPear")))
    fragment = asyncio.run(FragmentClient(http, MagicMock()).send_stars("someone", 50, uuid4()))

    assert platega.pay_url == paypear.pay_url == "https://test.link"
    assert fragment["status"] == "CREATED"
    assert http.method_calls == []


def test_real_provider_calls_in_prod(settings):
    settings.IS_DEBUG = False
    assert not PlategaClient(MagicMock()).debug
    assert not PayPearClient(MagicMock()).debug
    assert not FragmentClient(MagicMock(), MagicMock()).debug


def test_fragment_response_url(settings):
    settings.SITE_DOMAIN = "https://shop.example/"
    settings.FRAGMENT_WEBHOOK_SECRET = "tok"
    transaction_id = uuid4()

    url = FragmentClient(MagicMock(), MagicMock()).build_response_url(transaction_id)

    assert url == f"https://shop.example/core/webhooks/fragment/?tx_id={transaction_id}&token=tok"


def _paypear_api_object(transaction_id: UUID, status: str) -> dict[str, object]:
    return {"order_id": str(transaction_id), "status": status, "metadata": build_paypear_metadata(_payload("PayPear"))}


def test_forged_paypear_confirmation_uses_status_from_paypear_api(pipeline):
    # Пользователь видит в боте ID заказа и шлёт поддельный CONFIRMED
    transaction_id = uuid4()
    pipeline.paypear.get_verified_payment = AsyncMock(return_value=_paypear_api_object(transaction_id, "NEW"))

    response = asyncio.run(views.paypear_webhook(_paypear_request(transaction_id, status="CONFIRMED")))

    assert response.status_code == 200
    pipeline.paypear.get_verified_payment.assert_awaited_once()
    assert pipeline.paypear.get_verified_payment.await_args.args[0] == str(transaction_id)
    pipeline.save.assert_awaited_once_with(ServicesNames.PAYPEAR, transaction_id, str(TransactionStatus.PENDING))


def test_paypear_webhook_for_payment_unknown_to_paypear_is_not_processed(pipeline):
    pipeline.paypear.get_verified_payment = AsyncMock(return_value=None)

    response = asyncio.run(views.paypear_webhook(_paypear_request(uuid4())))

    assert response.status_code == 404
    pipeline.save.assert_not_awaited()
    pipeline.task.apply_async.assert_not_called()


def test_paypear_webhook_asks_for_retry_when_paypear_api_is_down(pipeline):
    from core.integrations.paypear.errors import PayPearAPINetworkError
    pipeline.paypear.get_verified_payment = AsyncMock(side_effect=PayPearAPINetworkError("down"))

    response = asyncio.run(views.paypear_webhook(_paypear_request(uuid4())))

    assert response.status_code == 503
    pipeline.save.assert_not_awaited()
    pipeline.task.apply_async.assert_not_called()


def test_paypear_client_verifies_through_api_only_in_prod(settings):
    import httpx
    transaction_id = str(uuid4())
    webhook_object = {"order_id": transaction_id, "status": "CONFIRMED"}

    settings.IS_DEBUG = True
    http = MagicMock()
    assert asyncio.run(PayPearClient(http).get_verified_payment(transaction_id, webhook_object)) is webhook_object
    assert http.method_calls == []

    settings.IS_DEBUG = False
    settings.PAYPEAR_API_URL = "https://api.paypear.ru/v1/"
    api_object = {"order_id": transaction_id, "status": "NEW"}
    http.get.return_value = httpx.Response(200, json={"success": True, "result": api_object})
    assert asyncio.run(PayPearClient(http).get_verified_payment(transaction_id, webhook_object)) == api_object
    assert http.get.call_args.args[0] == f"https://api.paypear.ru/v1/payment/order/{transaction_id}/"

    http.get.return_value = httpx.Response(404)
    assert asyncio.run(PayPearClient(http).get_verified_payment(transaction_id, webhook_object)) is None


# Fragment: ключ идемпотентности освобождается, если обработка не дошла до 200

@pytest.fixture
def fragment_redis(monkeypatch, pipeline, settings):
    import core.integrations.webhook_utils as webhook_utils
    settings.FRAGMENT_WEBHOOK_SECRET = "tok"
    redis = MagicMock(set=AsyncMock(return_value=True), delete=AsyncMock(return_value=1))
    monkeypatch.setattr(webhook_utils, "get_async_redis_client", lambda: redis)
    monkeypatch.setattr(views, "update_fragment_tx_task", MagicMock())
    return redis


def _fragment_callback(transaction_id: UUID):
    return RequestFactory().post(
        f"/core/webhooks/fragment/?tx_id={transaction_id}&token=tok",
        data=json.dumps({"id": str(uuid4()), "status": "COMPLETED"}), content_type="application/json",
        headers={"X-Idempotency-Key": "idem-1"},
    )


def test_fragment_callback_rejected_for_retry_releases_idempotency_key(pipeline, fragment_redis):
    pipeline.acquire.return_value = None

    response = asyncio.run(views.fragment_webhook(_fragment_callback(uuid4())))

    assert response.status_code == 429
    fragment_redis.delete.assert_awaited_once_with("fragment_idem_key:idem-1")


def test_processed_fragment_callback_keeps_idempotency_key(pipeline, fragment_redis):
    transaction_id = uuid4()

    response = asyncio.run(views.fragment_webhook(_fragment_callback(transaction_id)))

    assert response.status_code == 200
    fragment_redis.set.assert_awaited_once_with("fragment_idem_key:idem-1", "1", ex=172800, nx=True)
    fragment_redis.delete.assert_not_awaited()
    pipeline.save.assert_any_await(ServicesNames.FRAGMENT, transaction_id, str(TransactionStatus.SUCCESS))


def test_duplicate_fragment_callback_is_acknowledged_without_processing(pipeline, fragment_redis):
    fragment_redis.set.return_value = False

    response = asyncio.run(views.fragment_webhook(_fragment_callback(uuid4())))

    assert response.status_code == 200
    pipeline.save.assert_not_awaited()
    fragment_redis.delete.assert_not_awaited()
