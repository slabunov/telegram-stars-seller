from typing import NotRequired, TypedDict


class PayHotPaymentRequestJSON(TypedDict):
    merchant_order_reference: str
    amount_minor: str
    currency: str
    payment_method: str


class PayHotPaymentObjectJSON(TypedDict):
    id: str
    status: str
    merchant_order_reference: str
    amount_minor: str
    currency: str
    payment_method: NotRequired[str]
    expires_at: NotRequired[str]


class PayHotCheckoutJSON(TypedDict):
    url: str
    expires_at: NotRequired[str | None]


class PayHotPaymentResponseJSON(TypedDict):
    payment: PayHotPaymentObjectJSON
    checkout: NotRequired[PayHotCheckoutJSON]


class PayHotErrorJSON(TypedDict):
    code: str
    message: str


class PayHotErrorResponseJSON(TypedDict):
    error: PayHotErrorJSON


class PayHotWebhookRequestJSON(TypedDict):
    api_version: str
    event_id: str
    event_type: str
    occurred_at: str
    data: PayHotPaymentObjectJSON
