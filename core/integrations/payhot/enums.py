from enum import StrEnum

from core.domain.enums import TransactionStatus


class PayHotStatus(StrEnum):
    REQUIRES_PAYMENT_METHOD = "requires_payment_method"
    REQUIRES_ACTION = "requires_action"
    PROCESSING = "processing"
    AUTHORIZED = "authorized"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    # ещё выясняется итог, окончательного отказа нет
    MANUAL_RECONCILIATION = "manual_reconciliation"

    @staticmethod
    def transform_into_internal_status_or_keep_original(payhot_status: str) -> TransactionStatus | str:
        if payhot_status == PayHotStatus.SUCCEEDED:
            return TransactionStatus.PROCESSING

        elif payhot_status in (PayHotStatus.FAILED, PayHotStatus.CANCELLED):
            return TransactionStatus.CANCELLED

        elif payhot_status in (PayHotStatus.REFUNDED, PayHotStatus.PARTIALLY_REFUNDED):
            return TransactionStatus.CHARGEBACKED

        elif payhot_status in (
                PayHotStatus.REQUIRES_PAYMENT_METHOD, PayHotStatus.REQUIRES_ACTION, PayHotStatus.PROCESSING,
                PayHotStatus.AUTHORIZED, PayHotStatus.MANUAL_RECONCILIATION
        ):
            return TransactionStatus.PENDING

        return payhot_status


# Коды `payment_method` из документации PayHot
PAYHOT_PAYMENT_METHODS = frozenset(("card", "sbp", "sberpay", "crypto", "applepay", "googlepay"))
