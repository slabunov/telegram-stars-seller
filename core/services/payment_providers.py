from typing import Protocol, final

from core.domain.schemas.payment import PaymentRequest
from core.dto.payment import PaymentDTO


class UnknownPaymentProviderError(LookupError):
    """`PaymentAPI.name` не соответствует ни одному зарегистрированному провайдеру."""


class InvalidPaymentMethodError(ValueError):
    """`PaymentMethod.external_id` не подходит провайдеру (например, не число для Platega)."""


class PaymentProvider(Protocol):
    key: str
    # Порядок методов оплаты в боте: меньше = выше
    display_priority: int

    async def create_payment(self, external_method_id: str, request: PaymentRequest) -> PaymentDTO:
        ...


@final
class PaymentProviderRegistry:
    def __init__(self, *providers: PaymentProvider) -> None:
        self._providers = {provider.key: provider for provider in providers}

    def get(self, key: str) -> PaymentProvider:
        try:
            return self._providers[key]
        except KeyError:
            raise UnknownPaymentProviderError(
                f"Unknown payment provider {key!r}; registered: {', '.join(self._providers)}"
            ) from None
