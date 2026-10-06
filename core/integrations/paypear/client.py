import asyncio
import httpx
import logging
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from django.conf import settings
from typing import cast, final, NoReturn
from urllib.parse import urljoin
from uuid import uuid4

from core.domain.network_utils import SAFE_TO_RETRY
from core.domain.schemas.payment import PaymentPayloadDict
from core.dto.payment import PaymentDTO
from core.integrations.paypear.errors import PayPearAPIError, PayPearAPINetworkError
from core.integrations.paypear.schemas import (
    PayPearPaymentObjectJSON,
    PayPearPaymentRequestJSON,
    PayPearPaymentResponseJSON,
    build_paypear_metadata,
)
from core.integrations.utils import build_site_url, create_new_timeout_conf_or_use_default

logger = logging.getLogger(__name__)


TIMEOUT = httpx.Timeout(timeout=15.0, connect=10.0)
LIMITS = httpx.Limits(max_keepalive_connections=10, keepalive_expiry=15.0)


PAYPEAR_WEBHOOK = "paypear_webhook"

DEFAULT_EXPIRES_IN = "00:30:00"
_PAYMENT_LIFETIME = timedelta(minutes=30)


def extract_payment_object(data: PayPearPaymentResponseJSON) -> PayPearPaymentObjectJSON | None:
    return data.get("result") or data.get("response")


@final
class PayPearClient:
    CREATE_PAYMENT_PATH = "payment/"
    ORDER_INFO_PATH = "payment/order/{order_id}/"

    def __init__(self, client: httpx.Client) -> None:
        self.url = cast(str, settings.PAYPEAR_API_URL)
        self.shop_id = cast(str, settings.PAYPEAR_SHOP_ID)
        self.secret = cast(str, settings.PAYPEAR_SECRET)
        self.return_url = cast(str, settings.TELEGRAM_CHANNEL_LINK) or cast(str, settings.SITE_DOMAIN)
        self.debug = cast(bool, settings.IS_DEBUG)

        self._client = client

    @staticmethod
    def build_webhook_url() -> str:
        return build_site_url(PAYPEAR_WEBHOOK)

    async def create_payment(
            self,
            method_type: str,
            amount: float,
            currency: str,
            description: str,
            payload: PaymentPayloadDict,
            *,
            timeout: float | None = None,
            connect: float | None = None
    ) -> PaymentDTO:
        """
        Создаёт платёж в PayPear.
        """
        order_id = uuid4()

        if self.debug:
            return PaymentDTO(
                transaction_id=order_id,
                pay_url="https://test.link",
                price=Decimal(str(amount)),
                expires_in=DEFAULT_EXPIRES_IN
            )

        expires_at = datetime.now(timezone.utc) + _PAYMENT_LIFETIME

        data: PayPearPaymentRequestJSON = {
            "order_id": str(order_id),
            "amount": {
                "value": f"{amount:.2f}",
                "currency": currency,
            },
            "confirmation": {
                "type": "redirect",
                "return_url": self.return_url,
            },
            "payment_method_data": {
                "type": method_type,
            },
            "description": description[:128],
            "metadata": build_paypear_metadata(payload),
            "expires_at": expires_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "webhook_url": self.build_webhook_url(),
        }

        response = await self._request(
            "POST", self.CREATE_PAYMENT_PATH, data, idempotence_key=str(order_id),
            timeout=timeout, connect=connect
        )

        if response.status_code == 200:
            response_data = cast(PayPearPaymentResponseJSON, response.json())
            payment_object = extract_payment_object(response_data)
            if payment_object is None:
                logger.exception(f"Неожиданный ответ PayPear без объекта платежа:\n{response_data = }")
                raise PayPearAPIError(f"PayPear не вернул объект платежа:\n{response_data = }")

            confirmation = payment_object.get("confirmation") or {}
            pay_url = confirmation.get("confirmation_url")

            return PaymentDTO(
                transaction_id=order_id,
                pay_url=pay_url,
                price=_parse_amount(payment_object, fallback=amount),
                expires_in=_parse_expires_in(payment_object, fallback=expires_at)
            )

        _raise_for_error(response, data)

    async def get_verified_payment(
            self,
            order_id: str,
            webhook_object: PayPearPaymentObjectJSON,
            *,
            timeout: float | None = 10.0,
            connect: float | None = 5.0
    ) -> PayPearPaymentObjectJSON | None:
        """
        Платёж по нашему `order_id` из API PayPear.

        Returns:
            объект платежа или `None`, если PayPear такой платёж не знает (404)
        """
        if self.debug:
            return webhook_object

        path = self.ORDER_INFO_PATH.format(order_id=order_id)
        response = await self._request("GET", path, timeout=timeout, connect=connect)

        if response.status_code == 200:
            return extract_payment_object(cast(PayPearPaymentResponseJSON, response.json()))

        if response.status_code == 404:
            return None

        _raise_for_error(response, {"order_id": order_id})

    async def _request(
            self,
            method: str,
            path: str,
            data: Mapping[str, object] | None = None,
            *,
            idempotence_key: str | None = None,
            timeout: float | None = None,
            connect: float | None = None
    ) -> httpx.Response:
        full_url = urljoin(self.url, path)
        timeout_conf = create_new_timeout_conf_or_use_default(timeout, connect, TIMEOUT)
        auth = (self.shop_id, self.secret)

        def do_sync_request() -> httpx.Response:
            if method == "POST":
                return self._client.post(
                    full_url,
                    json=data, auth=auth, timeout=timeout_conf,
                    headers={"Idempotence-Key": idempotence_key or "", "Content-Type": "application/json"},
                )

            return self._client.get(full_url, auth=auth, timeout=timeout_conf)

        try:
            response = await asyncio.to_thread(do_sync_request)

        except (*SAFE_TO_RETRY, ) as exc:
            err_msg = "Произошла ошибка соединения при обращении к PayPear"
            logger.exception(err_msg)
            raise PayPearAPINetworkError(err_msg) from exc

        except httpx.TimeoutException as exc:
            logger.exception("Превышено время ожидания при обращении к PayPear")
            raise PayPearAPIError("Превышено время ожидания при обращении к PayPear") from exc

        except httpx.HTTPError as exc:
            logger.exception(f"Ошибка HTTP при обращении к PayPear: {exc}")
            raise PayPearAPIError(f"Ошибка HTTP при обращении к PayPear: {exc}") from exc

        return response


def _parse_amount(payment_object: PayPearPaymentObjectJSON, *, fallback: float) -> Decimal:
    amount = payment_object.get("amount") or {}
    value = amount.get("value")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(str(fallback))


def _parse_expires_in(payment_object: PayPearPaymentObjectJSON, *, fallback: datetime) -> str:
    raw = payment_object.get("expires_at")
    expires_at = fallback
    if raw:
        try:
            expires_at = datetime.fromisoformat(raw)
        except ValueError:
            pass

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    remaining = expires_at - datetime.now(timezone.utc)
    total_seconds = int(remaining.total_seconds())
    # дальше по коду expires_in парсится через strptime("%H:%M:%S"), где %H не принимает >23,
    # поэтому за пределами суток отдаём безопасное значение по умолчанию
    if total_seconds <= 0 or total_seconds >= 24 * 3600:
        return DEFAULT_EXPIRES_IN

    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _raise_for_error(response: httpx.Response, request_data: Mapping[str, object]) -> NoReturn:
    try:
        body = cast(PayPearPaymentResponseJSON, response.json())
        error = body.get("error") or {}
        detail = f"{error.get('code')}: {error.get('message')}"
    except Exception:
        detail = response.text[:500]

    if response.status_code in (401, 403):
        logger.exception(f"Не удалось авторизоваться в PayPear: {detail}")
        raise PayPearAPIError(f"Не удалось авторизоваться в PayPear: {detail}")

    if response.status_code == 400:
        logger.exception(f"Ошибка валидации при обращении к PayPear: {detail}\n{request_data = }")
        raise PayPearAPIError(f"Ошибка валидации при обращении к PayPear: {detail}")

    logger.exception(f"Неизвестная ошибка PayPear ({response.status_code}): {detail}\n{request_data = }")
    raise PayPearAPIError(f"Неизвестная ошибка PayPear ({response.status_code}): {detail}")
