from __future__ import annotations

from uuid import UUID
from typing import cast, ParamSpec, TypeVar

from core.integrations.fragment.enums import FragmentStatus
from core.integrations.fragment.webhook_utils import (
    safe_get_fragment_tx_with_retries,
    safe_create_fragment_tx_with_retries,
    safe_set_status_for_fragment_tx_id_with_retries
)
from core.tasks import Task


P = ParamSpec("P")
R = TypeVar("R")


async def update_fragment_transaction_workflow(
        celery_task: Task[P,R],
        fragment_tx_id: UUID, transaction_id: UUID,
        new_status: str,
        *,
        started_at: float
) -> tuple[bool, str]:
    kwargs = cast(dict[str, object], celery_task.request.kwargs or {}).copy()  # noqa
    kwargs["started_at"] = started_at

    new_status = cast(FragmentStatus, new_status)

    timeout = 300.0  # 5 минут

    transaction = await safe_get_fragment_tx_with_retries(
        celery_task, started_at, kwargs,
        timeout,
        fragment_tx_id
    )
    if transaction is None:
        transaction = await safe_create_fragment_tx_with_retries(
            celery_task, started_at, kwargs, timeout,
            fragment_tx_id, transaction_id, new_status
        )
        if transaction is None:
            return False, f"fragment transaction {fragment_tx_id} not found and is not recreated"

    if transaction.status == new_status:
        return True, f"fragment transaction {fragment_tx_id} is already {new_status}"

    final_statuses = (FragmentStatus.COMPLETED, FragmentStatus.FAILED)
    if transaction.status in final_statuses and new_status not in final_statuses:
        return True, f"fragment transaction {fragment_tx_id} is final ({transaction.status}), ignored {new_status}"

    is_changed = await safe_set_status_for_fragment_tx_id_with_retries(
        celery_task, started_at, kwargs, timeout,
        fragment_tx_id, new_status
    )

    if is_changed:
        return True, f"fragment transaction {fragment_tx_id} set status to {new_status}"

    return False, f"fragment transaction {fragment_tx_id} is not changed status from {transaction.status} to {new_status}"
