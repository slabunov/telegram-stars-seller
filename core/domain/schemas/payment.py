"""
Общие для всех платёжных провайдеров типы.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, TypedDict


class PaymentPayloadDict(TypedDict):
    """
    Контекст заказа, который передаётся провайдеру при создании платежа и возвращается в вебхуке
    (Platega - строка `payload`, PayPear - `metadata`), а также сохраняется в транзакции.
    """
    user_id: int
    message_id: int
    price: float
    stars_count: int
    target_username: str
    payment_api: str
    pay_url: str
    promo_id: int | None
    promo_name: str
    promo_discount: Annotated[str, Decimal] | None


@dataclass(frozen=True, slots=True)
class PaymentPayloadValidateModel:
    user_id: int
    message_id: int
    price: float
    stars_count: int
    target_username: str
    payment_api: str
    pay_url: str
    promo_id: int | None
    promo_name: str
    promo_discount: Annotated[str, Decimal] | None


@dataclass(frozen=True, slots=True)
class PaymentRequest:
    """Провайдер-нейтральные данные для создания платежа."""
    amount: Decimal
    currency: str
    description: str
    buyer_username: str
    payload: PaymentPayloadDict
