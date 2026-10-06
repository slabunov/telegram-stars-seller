import asyncio
import httpx
import logging
import re
from collections.abc import Mapping
from decimal import Decimal
from django.conf import settings
from typing import cast, final
from urllib.parse import urljoin
from uuid import UUID, uuid4

from core.domain.network_utils import SAFE_TO_RETRY
from core.dto.payment import PaymentDTO
from core.integrations.platega.errors import PlategaAPIError, PlategaAPINetworkError
from core.integrations.platega.schemas import PaymentRequestJSON, PaymentRequestMetadataJSON, \
    TransactionCreationResponse
from core.integrations.utils import create_new_timeout_conf_or_use_default

logger = logging.getLogger(__name__)


TIMEOUT = httpx.Timeout(timeout=15.0, connect=10.0)
LIMITS = httpx.Limits(max_keepalive_connections=10, keepalive_expiry=15.0)


PLATEGA_WEBHOOK = "platega_webhook"


@final
class PlategaClient:
    PAYMENT_WITH_METHOD_PATH = "transaction/process"

    def __init__(self, client: httpx.Client) -> None:
        self.url = cast(str, getattr(settings, "PLATEGA_API_URL", None))  # noqa
        self.merchant_id = cast(str, getattr(settings, "PLATEGA_MERCHANT_ID", None))  # noqa
        self.secret = cast(str, getattr(settings, "PLATEGA_SECRET", None))  # noqa
        self.debug = cast(bool, settings.IS_DEBUG)

        if not all([self.url, self.merchant_id, self.secret]):
            logger.error("PlategaClient не сконфигурирован.")
            raise ValueError("PlategaClient is not configured properly")

        self._client = client

    async def create_payment(
            self,
            payment_method: int,
            amount: float,
            currency: str,
            description: str,
            telegram_id: str,
            username: str,
            payload: str,
            *,
            timeout: float | None = None,
            connect: float | None = None
    ) -> PaymentDTO:
        if self.debug:
            return PaymentDTO(
                transaction_id=uuid4(),
                pay_url="https://test.link",
                price=Decimal(amount),
                expires_in="00:30:00"
            )

        if not payload:
            logger.exception("payload пуст при создании транзакции в платеге")
            raise PlategaAPIError("payload пуст при создании транзакции в платеге")

        method = "POST"

        metadata: PaymentRequestMetadataJSON = {
            "userId": str(telegram_id),
            "userName": str(username),
        }
        data: PaymentRequestJSON = {
            "paymentMethod": payment_method,
            "paymentDetails": {
                "amount": amount,
                "currency": currency,
            },
            "description": description,
            "payload": payload,
            "metadata": metadata
        }

        response = await self._make_request(
            method,
            self.PAYMENT_WITH_METHOD_PATH,
            data,
            timeout=timeout, connect=connect
        )

        if response.status_code == 200:
            response_data = cast(TransactionCreationResponse, response.json())

            payment_details = response_data["paymentDetails"]
            if payment_details is None:
                price = amount

            elif isinstance(payment_details, dict):
                payment_amount = payment_details["amount"]
                if payment_amount is None:
                    price = amount
                else:
                    price = str(payment_amount)

            else:
                price_pattern = re.compile(r"^(\S+)")
                price = price_pattern.match(payment_details).group()

            default_expires_in = "00:30:00"
            expires_in = response_data.get("expiresIn", default_expires_in)
            if expires_in is None:
                expires_in = default_expires_in

            return PaymentDTO(
                transaction_id=UUID(response_data["transactionId"]),
                pay_url=response_data["redirect"],
                price=Decimal(price),
                expires_in=expires_in
            )

        if response.status_code == 400:
            logger.exception("Ошибка валидации во время создания платежа на Platega")
            raise PlategaAPIError(f"Ошибка валидации во время создания платежа:\n{data = }")

        if response.status_code == 401:
            logger.exception("Не удалось авторизоваться во время создания платежа на Platega")
            raise PlategaAPIError("Не удалось авторизоваться во время создания платежа")

        logger.exception("Неизвестная ошибка во время создания платежа на Platega")
        raise PlategaAPIError(f"Неизвестная ошибка во время создания платежа:\n{data = }\n{response = }")

    async def _make_request(
            self,
            method: str,
            path: str,
            data: Mapping[str, object] | None = None,
            *,
            timeout: float | None = None,
            connect: float | None = None
    ) -> httpx.Response:
        headers = self._get_headers(method)
        full_url = urljoin(self.url, path)
        timeout_conf = create_new_timeout_conf_or_use_default(timeout, connect, TIMEOUT)

        def do_sync_request() -> httpx.Response:
            if method == "POST":
                return self._client.post(
                    full_url,
                    json=data, headers=headers,
                    timeout=timeout_conf
                )

            return self._client.get(full_url, headers=headers, timeout=timeout_conf)

        try:
                response = await asyncio.to_thread(do_sync_request)

        except (*SAFE_TO_RETRY, ) as exc:
            err_msg = "Произошла ошибка соединения при обращении к Platega"
            logger.exception(err_msg)
            raise PlategaAPINetworkError(err_msg) from exc

        except httpx.TimeoutException as exc:
            logger.exception(f"Превышено время ожидания при попытке создать платёж на Platega")
            raise PlategaAPIError("Превышено время ожидания при попытке создать платёж") from exc

        except httpx.HTTPError as exc:
            logger.exception(f"Ошибка HTTP во время создания платежа на Platega: {exc}")
            raise PlategaAPIError(f"Ошибка HTTP во время создания платежа: {exc}") from exc

        return response

    def _get_headers(self, method: str, path: str = ""):
        if path:
            raise NotImplementedError("headers for concrete path is not supported now")

        if method == "POST":
            return {
                "X-MerchantId": self.merchant_id,
                "X-Secret": self.secret,
                "Content-Type": "application/json",
            }
        elif method == "GET":
            return {
                "X-MerchantId": self.merchant_id,
                "X-Secret": self.secret,
            }
        else:
            raise ValueError(f"Invalid method for headers: {method}")
