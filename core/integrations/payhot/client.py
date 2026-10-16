import asyncio
import httpx
import logging
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from typing import cast, final, NoReturn
from urllib.parse import urljoin
from uuid import uuid4

from core.domain.network_utils import SAFE_TO_RETRY
from core.dto.payment import PaymentDTO
from core.integrations.payhot.errors import PayHotAPIError, PayHotAPINetworkError
from core.integrations.payhot.schemas import (
    PayHotErrorResponseJSON,
    PayHotPaymentRequestJSON,
    PayHotPaymentResponseJSON,
)
from core.integrations.utils import DEFAULT_EXPIRES_IN, create_new_timeout_conf_or_use_default, format_expires_in

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(timeout=15.0, connect=10.0)
LIMITS = httpx.Limits(max_keepalive_connections=10, keepalive_expiry=15.0)

PAYHOT_WEBHOOK = "payhot_webhook"

_PAYMENT_LIFETIME = timedelta(minutes=30)
_MINOR_UNITS = Decimal(100)


def to_amount_minor(amount: Decimal) -> str:
    return str(int((amount * _MINOR_UNITS).quantize(Decimal(1), rounding=ROUND_HALF_UP)))


@final
class PayHotClient:
    CREATE_PAYMENT_PATH = "payments"

    def __init__(self, client: httpx.Client) -> None:
        self.url = cast(str, settings.PAYHOT_API_URL)
        self.api_key = cast(str, settings.PAYHOT_API_KEY)
        self.debug = cast(bool, settings.IS_DEBUG)

        self._client = client

    async def create_payment(
            self,
            method: str,
            amount: Decimal,
            currency: str,
            *,
            timeout: float | None = None,
            connect: float | None = None
    ) -> PaymentDTO:
        """
        Создаёт платёж в PayHot.
        """
        order_id = uuid4()

        if self.debug:
            return PaymentDTO(
                transaction_id=order_id,
                pay_url="https://test.link",
                price=amount,
                expires_in=DEFAULT_EXPIRES_IN
            )

        fallback_expires_at = datetime.now(timezone.utc) + _PAYMENT_LIFETIME

        data: PayHotPaymentRequestJSON = {
            "merchant_order_reference": str(order_id),
            "amount_minor": to_amount_minor(amount),
            "currency": currency,
            "payment_method": method,
        }

        response = await self._request(
            urljoin(self.url, self.CREATE_PAYMENT_PATH), data, idempotency_key=str(order_id),
            timeout=timeout, connect=connect
        )

        if response.status_code in (200, 201):
            response_data = cast(PayHotPaymentResponseJSON, response.json())
            payment = response_data.get("payment") or {}
            checkout = response_data.get("checkout") or {}

            try:
                price = Decimal(payment["amount_minor"]) / _MINOR_UNITS
            except Exception:
                price = amount

            return PaymentDTO(
                transaction_id=order_id,
                pay_url=checkout.get("url"),
                price=price,
                expires_in=format_expires_in(checkout.get("expires_at"), fallback=fallback_expires_at),
                provider_payment_id=payment.get("id")
            )

        _raise_for_error(response, data)

    async def _request(
            self,
            full_url: str,
            data: Mapping[str, object],
            *,
            idempotency_key: str,
            timeout: float | None = None,
            connect: float | None = None
    ) -> httpx.Response:
        timeout_conf = create_new_timeout_conf_or_use_default(timeout, connect, TIMEOUT)

        def do_sync_request() -> httpx.Response:
            return self._client.post(
                full_url,
                json=data, timeout=timeout_conf,
                headers={"Authorization": f"Bearer {self.api_key}", "Idempotency-Key": idempotency_key},
            )

        try:
            response = await asyncio.to_thread(do_sync_request)

        except (*SAFE_TO_RETRY,) as exc:
            err_msg = "Произошла ошибка соединения при обращении к PayHot"
            logger.exception(err_msg)
            raise PayHotAPINetworkError(err_msg) from exc

        except httpx.TimeoutException as exc:
            logger.exception("Превышено время ожидания при обращении к PayHot")
            raise PayHotAPIError("Превышено время ожидания при обращении к PayHot") from exc

        except httpx.HTTPError as exc:
            logger.exception(f"Ошибка HTTP при обращении к PayHot: {exc}")
            raise PayHotAPIError(f"Ошибка HTTP при обращении к PayHot: {exc}") from exc

        return response


def _raise_for_error(response: httpx.Response, request_data: Mapping[str, object]) -> NoReturn:
    try:
        error = cast(PayHotErrorResponseJSON, response.json()).get("error") or {}
        detail = f"{error.get('code')}: {error.get('message')}"
    except Exception:
        detail = response.text[:500]

    if response.status_code in (401, 403):
        logger.exception(f"Не удалось авторизоваться в PayHot: {detail}")
        raise PayHotAPIError(f"Не удалось авторизоваться в PayHot: {detail}")

    if response.status_code in (400, 409, 422):
        logger.exception(f"Ошибка валидации при обращении к PayHot: {detail}\n{request_data = }")
        raise PayHotAPIError(f"Ошибка валидации при обращении к PayHot: {detail}")

    logger.exception(f"Неизвестная ошибка PayHot ({response.status_code}): {detail}\n{request_data = }")
    raise PayHotAPIError(f"Неизвестная ошибка PayHot ({response.status_code}): {detail}")
