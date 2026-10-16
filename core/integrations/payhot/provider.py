from typing import final

from core.domain.schemas.payment import PaymentRequest
from core.dto.payment import PaymentDTO
from core.integrations.payhot.client import PayHotClient
from core.integrations.payhot.enums import PAYHOT_PAYMENT_METHODS
from core.services.payment_providers import InvalidPaymentMethodError

PAYHOT = "PayHot"


@final
class PayHotPaymentProvider:
    key = PAYHOT
    display_priority = -1

    def __init__(self, client: PayHotClient) -> None:
        self._client = client

    async def create_payment(self, external_method_id: str, request: PaymentRequest) -> PaymentDTO:
        if external_method_id not in PAYHOT_PAYMENT_METHODS:
            raise InvalidPaymentMethodError(
                f"PayHot expects one of {sorted(PAYHOT_PAYMENT_METHODS)}, got {external_method_id!r}"
            )

        return await self._client.create_payment(external_method_id, request.amount, request.currency)
