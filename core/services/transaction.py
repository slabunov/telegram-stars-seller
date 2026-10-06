import logging
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import final
from uuid import UUID

from core.domain.enums import TransactionStatus
from core.domain.schemas.payment import PaymentPayloadDict
from core.models import Transaction
from core.repositories.transaction import TransactionRepository
from core.repositories.user import UserRepository
from core.services.user import UnregisteredUser

logger = logging.getLogger(__name__)


@final
class TransactionService:
    def __init__(self, trans_repo: TransactionRepository, user_repo: UserRepository):
        self._trans_repo = trans_repo
        self._user_repo = user_repo

    async def create_transaction(
            self,
            transaction_id: UUID | str,
            parsed_payload: PaymentPayloadDict, payment_method: str,
            status: TransactionStatus = TransactionStatus.PENDING, payload: Mapping[str, object] | None = None,
            expires_in: str = ""
    ) -> Transaction:
        """Создаёт транзакцию с нуля"""

        if not isinstance(transaction_id, UUID):
            transaction_id = UUID(transaction_id)

        transaction = await self._trans_repo.get_by_transaction_id(transaction_id)
        if transaction:
            return transaction

        telegram_user = await self._user_repo.get_by_telegram_id(parsed_payload["user_id"])
        if telegram_user is None:
            raise UnregisteredUser(parsed_payload["user_id"])

        target_username = parsed_payload["target_username"]
        if telegram_user.username == target_username:
            target_username = ""  # Для использования TARGET_SELF

        promo_discount = parsed_payload["promo_discount"]
        if promo_discount is not None:
            promo_discount = Decimal(promo_discount)

        return await self._trans_repo.create_transaction(
            transaction_id=transaction_id,
            user=telegram_user,
            amount_fiat=parsed_payload["price"],
            amount_stars=parsed_payload["stars_count"],
            payment_method=f"{parsed_payload["payment_api"]} - {payment_method}",
            pay_url=parsed_payload["pay_url"],
            message_id=parsed_payload["message_id"],
            promo_id=parsed_payload["promo_id"],
            promo_name=parsed_payload["promo_name"],
            promo_discount=promo_discount,
            target_username=target_username,
            status=status,
            json_payload=payload,
            expires_in=expires_in
        )

    async def get_transaction(
            self,
            transaction_id: UUID,
            is_select_user: bool = True,
            is_select_metadata: bool = True
    ) -> Transaction | None:
        return await self._trans_repo.get_by_transaction_id(transaction_id, is_select_user, is_select_metadata)

    async def get_status(self, transaction_id: UUID) -> str | None:
        transaction = await self.get_transaction(transaction_id, is_select_user=False, is_select_metadata=False)
        return transaction.status if transaction is not None else None

    async def get_processing_or_succeeded_transactions(
            self,
            promo_id: int  | None = None,
            is_select_user: bool = True
    ) -> tuple[list[Transaction], int]:
        return await self._trans_repo.get_many_by(
            exclude_status=(
                TransactionStatus.CHARGEBACKED, TransactionStatus.CANCELLED, TransactionStatus.FAILED
            ),
            promo_id=promo_id,
            is_count=True,
            is_select_user=is_select_user, is_select_metadata=True
        )

    async def update_by_obj(
            self,
            transaction: Transaction,
            *,
            new_status: TransactionStatus | None = None, new_payload: dict[str, object] | None = None,
            is_count_transaction: bool = True,
            is_count_metadata: bool = True
    ) -> tuple[bool, Transaction]:
        return await self._trans_repo.update(
            transaction,
            new_status=new_status,
            new_payload=new_payload,
            is_count_transaction=is_count_transaction,
            is_count_metadata=is_count_metadata
        )

    async def update_by_id(
            self,
            transaction_id: UUID,
            *,
            new_status: TransactionStatus | None = None, new_payload: dict[str, object] | None = None,
            is_count_transaction: bool = True,
            is_count_metadata: bool = True
    ) -> bool:
        return await self._trans_repo.update(
            transaction_id,
            new_status=new_status,
            new_payload=new_payload,
            is_count_transaction=is_count_transaction,
            is_count_metadata=is_count_metadata
        )

    async def save_message_id(self, transaction_id: UUID, message_id: int) -> tuple[bool, Transaction | None]:
        transaction = await self.get_transaction(transaction_id, is_select_user=False, is_select_metadata=False)
        if transaction is None:
            return False, None

        return await self._trans_repo.update(transaction, message_id=message_id)

    async def delete_transaction(self, transaction: Transaction) -> None:
        await self._trans_repo.delete_transaction(transaction)

    # В данный момент не используется
    async def delete_expired_transactions(self) -> None:
        """Удаляет все транзакции со статусом PENDING, у которых истекло время ожидания."""
        await self._trans_repo.delete_expired_transactions(None)

    # В данный момент не используется
    async def delete_transactions_expires_in(self, expires_in: str, transaction_ids: list[UUID] | UUID | None = None) -> None:
        """
        Удаляет транзакции (или одну) со статусом PENDING, у которых истекло время ожидания.

        Arguments:

        - `expires_in` - имеет формат HH:MM:SS (%H:%M:%S в datetime).

        - `transaction_ids` - list[UUID] | UUID | None, если указано, то удалит либо транзакции с указанными ID,
        либо конкретную транзакцию, иначе удалит все найденные транзакции (в каждом случае проверяется
        статус PENDING и время жизни).
        """
        expires_in_td = datetime.strptime(expires_in, "%H:%M:%S")
        expires_in_td = timedelta(
            hours=expires_in_td.hour,
            minutes=expires_in_td.minute,
            seconds=expires_in_td.second
        )
        await self._trans_repo.delete_expired_transactions(expires_in_td, transaction_ids)
