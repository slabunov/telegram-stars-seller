"""PaymentService + реестр провайдеров: выбор провайдера, ID методов оплаты, расширяемость."""
import asyncio
import json
import pytest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from core.domain.schemas.payment import PaymentRequest
from core.dto.payment import PaymentDTO
from core.integrations.paypear.provider import PayPearPaymentProvider
from core.integrations.platega.provider import PlategaPaymentProvider
from core.services.payment import PaymentService
from core.services.payment_providers import (
    InvalidPaymentMethodError, PaymentProviderRegistry, UnknownPaymentProviderError
)


def _dto() -> PaymentDTO:
    return PaymentDTO(transaction_id=uuid4(), pay_url="https://pay", price=Decimal("100"), expires_in="00:30:00")


def _request(buyer_username: str = "buyer", target_username: str = "buyer") -> PaymentRequest:
    return PaymentRequest(
        amount=Decimal("100.50"), currency="RUB", description="d", buyer_username=buyer_username,
        payload={
            "user_id": 1, "message_id": 2, "price": 100.5, "stars_count": 50, "target_username": target_username,
            "payment_api": "X", "pay_url": "", "promo_id": None, "promo_name": "", "promo_discount": None,
        },
    )


def _registry() -> PaymentProviderRegistry:
    return PaymentProviderRegistry(PayPearPaymentProvider(MagicMock()), PlategaPaymentProvider(MagicMock()))


# Реестр

def test_registry_resolves_by_exact_payment_api_name():
    registry = _registry()
    assert isinstance(registry.get("Platega"), PlategaPaymentProvider)
    assert isinstance(registry.get("PayPear"), PayPearPaymentProvider)


@pytest.mark.parametrize("name", ["Stripe", "platega", "PayPear API", ""])
def test_unknown_provider_fails_clearly(name):
    with pytest.raises(UnknownPaymentProviderError,
                       match=f"Unknown payment provider {name!r}; registered: PayPear, Platega"):
        _ = _registry().get(name)


def _method(api_name: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(api=SimpleNamespace(name=api_name), name=name, external_id="1",
                           commission_percent=Decimal("0"))


@pytest.mark.parametrize("providers", [
    (PayPearPaymentProvider(MagicMock()), PlategaPaymentProvider(MagicMock())),
    (PlategaPaymentProvider(MagicMock()), PayPearPaymentProvider(MagicMock())),
], ids=["paypear-registered-first", "platega-registered-first"])
def test_paypear_methods_are_shown_first_regardless_of_registration_order(providers):
    db_order = [_method("Platega", "СБП"), _method("PayPear", "Карта"), _method("Platega", "Крипта"),
                _method("PayPear", "СБП")]
    payment_repo = MagicMock(get_many_by=AsyncMock(return_value=db_order))
    service = PaymentService(MagicMock(), MagicMock(), payment_repo, MagicMock(), PaymentProviderRegistry(*providers),
                             MagicMock())

    methods = asyncio.run(service.get_active_payment_methods())

    # внутри одного провайдера сохраняется порядок из БД
    assert [(m.api_name, m.name) for m in methods] == [
        ("PayPear", "Карта"), ("PayPear", "СБП"), ("Platega", "СБП"), ("Platega", "Крипта")
    ]


def test_priority_is_explicit_on_the_provider():
    assert PayPearPaymentProvider.display_priority < PlategaPaymentProvider.display_priority


def test_active_method_with_unknown_provider_is_hidden_and_logged(caplog):
    db_order = [_method("Stripe", "Карта"), _method("Platega", "СБП"), _method("PayPear", "СБП")]
    payment_repo = MagicMock(get_many_by=AsyncMock(return_value=db_order))
    service = PaymentService(MagicMock(), MagicMock(), payment_repo, MagicMock(), _registry(), MagicMock())

    methods = asyncio.run(service.get_active_payment_methods())

    # Валидные провайдеры показаны в своём приоритете, неизвестный скрыт с ERROR
    assert [(m.api_name, m.name) for m in methods] == [("PayPear", "СБП"), ("Platega", "СБП")]
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1 and "Unknown payment provider 'Stripe'" in errors[0].getMessage()


# Провайдеры

def test_platega_converts_method_id_to_int_and_sends_payload_as_json():
    client = MagicMock(create_payment=AsyncMock(return_value=_dto()))
    _ = asyncio.run(PlategaPaymentProvider(client).create_payment("2", _request()))

    args, kwargs = client.create_payment.await_args
    assert args == (2, 100.5, "RUB", "d", "1", "buyer")
    assert json.loads(kwargs["payload"])["stars_count"] == 50


def test_platega_gift_without_buyer_username():
    client = MagicMock(create_payment=AsyncMock(return_value=_dto()))
    _ = asyncio.run(
        PlategaPaymentProvider(client).create_payment("2", _request(buyer_username="", target_username="friend")))
    assert client.create_payment.await_args.args[5] == "отсутствует, но это подарок для friend"


def test_platega_rejects_non_integer_method_id_before_any_request():
    client = MagicMock(create_payment=AsyncMock())
    with pytest.raises(InvalidPaymentMethodError, match="Platega expects an integer"):
        _ = asyncio.run(PlategaPaymentProvider(client).create_payment("sbp", _request()))
    client.create_payment.assert_not_awaited()


def test_paypear_keeps_method_id_as_string():
    client = MagicMock(create_payment=AsyncMock(return_value=_dto()))
    request = _request()
    _ = asyncio.run(PayPearPaymentProvider(client).create_payment("sbp", request))
    client.create_payment.assert_awaited_once_with("sbp", 100.5, "RUB", "d", request.payload)


def test_paypear_rejects_empty_method_id():
    with pytest.raises(InvalidPaymentMethodError):
        _ = asyncio.run(PayPearPaymentProvider(MagicMock()).create_payment(" ", _request()))


# PaymentService не знает о конкретных провайдерах

class FakeProvider:
    key = "Fake"
    display_priority = 5

    def __init__(self) -> None:
        self.calls: list[tuple[str, PaymentRequest]] = []

    async def create_payment(self, external_method_id: str, request: PaymentRequest) -> PaymentDTO:
        self.calls.append((external_method_id, request))
        return _dto()


def _service(registry: PaymentProviderRegistry, username: str = "buyer") -> PaymentService:
    user_repo = MagicMock(get_by_telegram_id=AsyncMock(return_value=SimpleNamespace(username=username)))
    return PaymentService(MagicMock(), user_repo, MagicMock(), MagicMock(), registry, MagicMock())


def test_new_provider_needs_only_registration():
    fake = FakeProvider()
    service = _service(PaymentProviderRegistry(fake))
    promo = SimpleNamespace(id=7, name="SUMMER", discount=Decimal("5.00"))

    dto, payload = asyncio.run(service.create_payment(
        user_id=1, message_id=2, price=Decimal("99.90"), stars_count=50,
        payment_api="Fake", method="any-id", target_username="friend", promo=promo,
        # pyright: ignore[reportArgumentType]
    ))

    method_id, request = fake.calls[0]
    assert method_id == "any-id"
    assert request.amount == Decimal("99.90") and request.currency == "RUB" and request.buyer_username == "buyer"
    assert request.description == "Purchase of 50 stars for @friend (promocode: SUMMER 5.00%)"
    assert request.payload is payload
    assert payload["payment_api"] == "Fake" and payload["target_username"] == "friend"
    assert (payload["promo_id"], payload["promo_name"], payload["promo_discount"]) == (7, "SUMMER", "5.00")
    assert dto.pay_url == "https://pay"


def test_unknown_payment_api_fails_before_touching_the_user():
    service = _service(PaymentProviderRegistry(FakeProvider()))
    with pytest.raises(UnknownPaymentProviderError):
        _ = asyncio.run(service.create_payment(1, 2, Decimal("1"), 50, "Nope", "x"))
    service._user_repo.get_by_telegram_id.assert_not_awaited()  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
