import json
from typing import final

from core.domain.schemas.payment import PaymentRequest
from core.dto.payment import PaymentDTO
from core.integrations.platega.client import PlategaClient
from core.services.payment_providers import InvalidPaymentMethodError

PLATEGA = "Platega"


@final
class PlategaPaymentProvider:
    key = PLATEGA
    display_priority = 1

    def __init__(self, client: PlategaClient) -> None:
        self._client = client

    async def create_payment(self, external_method_id: str, request: PaymentRequest) -> PaymentDTO:
        # Platega ждёт числовой ID метода: 2 - СБП, 11 - карты, 12 - международные карты, 13 - криптовалюта
        try:
            method = int(external_method_id)
        except ValueError:
            raise InvalidPaymentMethodError(
                f"Platega expects an integer payment method id, got {external_method_id!r}"
            ) from None

        payload = request.payload
        username = request.buyer_username or f"отсутствует, но это подарок для {payload['target_username']}"

        return await self._client.create_payment(
            method,
            float(request.amount), request.currency,
            request.description,
            str(payload["user_id"]),
            username,
            payload=json.dumps(payload, ensure_ascii=False)
        )
