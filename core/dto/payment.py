from dataclasses import dataclass
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

from core.models import GlobalSettings, ExchangeRate


@dataclass(frozen=True)
class PaymentMethodDTO:
    api_name: str
    name: str
    external_id: str
    commission_percent: Decimal


@dataclass
class PaymentDTO:
    transaction_id: UUID
    pay_url: str | None
    price: Decimal
    expires_in: str
    provider_payment_id: str | None = None


class PricingDTO(NamedTuple):
    settings: GlobalSettings
    exchange_rate: ExchangeRate
