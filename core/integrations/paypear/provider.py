from typing import final

from core.domain.schemas.payment import PaymentRequest
from core.dto.payment import PaymentDTO
from core.integrations.paypear.client import PayPearClient
from core.services.payment_providers import InvalidPaymentMethodError

PAYPEAR = "PayPear"


@final
class PayPearPaymentProvider:
    key = PAYPEAR
    display_priority = 0  # PayPear должен отображаться выше Platega

    def __init__(self, client: PayPearClient) -> None:
        self._client = client

    async def create_payment(self, external_method_id: str, request: PaymentRequest) -> PaymentDTO:
        if not external_method_id.strip():
            raise InvalidPaymentMethodError("PayPear expects a non-empty payment method type")

        return await self._client.create_payment(
            external_method_id,
            float(request.amount), request.currency,
            request.description,
            request.payload
        )
