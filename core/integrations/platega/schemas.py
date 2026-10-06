from typing import NotRequired, TypedDict, Annotated
from uuid import UUID


class PaymentRequestDetailsJSON(TypedDict):
    amount: float
    currency: str


class PaymentRequestMetadataJSON(TypedDict):
    userId: str
    userName: str


class PaymentRequestJSON(TypedDict):
    paymentMethod: int
    paymentDetails: PaymentRequestDetailsJSON
    description: str
    payload: str
    metadata: PaymentRequestMetadataJSON


class TransactionCreationResponsePaymentDetailsJSON(TypedDict):
    amount: float | None
    currency: str | None


class TransactionCreationResponse(TypedDict):
    paymentMethod: str | None
    transactionId: Annotated[str, UUID]
    redirect: str | None
    paymentDetails: str | TransactionCreationResponsePaymentDetailsJSON | None
    expiresIn: str | None


class PlategaWebhookRequestJSON(TypedDict):
    id: str
    amount: float
    currency: str
    status: str
    paymentMethod: int | None
    payload: str


class WebhookCeleryKwargs(TypedDict):
    started_at: float
    extra: NotRequired[dict[str, object]]


class CeleryPollingTaskForProcessing(TypedDict):
    message_id: int
    final_status: str
    started_at: float | None
    is_sent_message_in_process: bool
    is_order_message_exists: bool
    is_awaited_final_status: bool
    retries: int
    max_retries: int
