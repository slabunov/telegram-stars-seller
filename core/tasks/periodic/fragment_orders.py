"""
Polls the Fragment API to get the final status of pending orders.

The Fragment system uses a webhook to send the final order status
to the 'response_url'. If the webhook fails because of network errors
or incorrect URLs, the order status stays in 'SEND_CREATED'.
Then, the user sees the message 'Order is processing...' and
the purchase history does not show the order.
The history only shows orders with the 'SUCCESS' status.

Every FRAGMENT_POLL_SECONDS, a Celery-beat task collects these pending
orders. The system sends a 'GET /order/{id}/' request to get the
current status. The system then sends this status to the same
process that handles the webhooks.
"""

from __future__ import annotations

import logging
import time
from asgiref.sync import async_to_sync
from celery import shared_task
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from typing import ParamSpec, TypeVar
from uuid import UUID

from core.domain.enums import TransactionStatus
from core.integrations.fragment.enums import FragmentStatus
from core.models import FragmentTransaction, Transaction
from core.tasks.utils import Task

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

_LOCK_NAME = "lock_poll_fragment"
_ACTED_PREFIX = "fragment:poll_acted:"
_ACTED_TTL = 30
_MAX_PER_TICK = 50

# Transaction statuses that a Fragment response can change to a final status
WAITING_STATUSES = (TransactionStatus.SEND_CREATED, TransactionStatus.IN_DOUBT)

# Fragment order statuses that allow the system to close a transaction
_FINAL_FRAGMENT_STATUSES = (FragmentStatus.COMPLETED, FragmentStatus.FAILED)


def decide_next_status(raw_status: str, age: timedelta, doubt_after: timedelta) -> str | None:
    """
    Finds the transaction status that matches the Fragment response.

    Returns:
        None if the order is still in progress. Otherwise, returns the new status.
    """
    if raw_status in _FINAL_FRAGMENT_STATUSES:
        return str(FragmentStatus.transform_into_internal_status_or_keep_original(raw_status))

    if age > doubt_after:
        # The order age is too high. The system changes the status to show
        # a support link to the user instead of "Order is processing...".
        # The poll task completes the order if Fragment closes it later.
        return str(TransactionStatus.IN_DOUBT)

    return None


def push_fragment_status(
        transaction_id: UUID,
        fragment_tx_id: UUID,
        raw_status: str,
        internal_status: str | None
) -> None:
    """
    Saves the new status to the Redis keys and tasks that the webhook uses.

    The 'internal_status' is None when the system must update only
    the 'FragmentTransaction' and keep the current transaction status.
    """
    from core.integrations.fragment.tasks import update_fragment_tx_task
    from core.integrations.platega.tasks import update_transaction_status_task
    from core.integrations.webhook_utils import ServicesNames
    from core.services.redis_service import sync_save_status_by_key

    _ = sync_save_status_by_key(ServicesNames.FRAGMENT__FROM_POLL, transaction_id, raw_status)
    _ = update_fragment_tx_task.apply_async(
        args=(str(fragment_tx_id), str(transaction_id)),
        kwargs={"started_at": None},
    )

    if internal_status is None:
        return

    _ = sync_save_status_by_key(ServicesNames.FRAGMENT, transaction_id, internal_status)
    _ = update_transaction_status_task.apply_async(
        args=(str(transaction_id), None, ""),
        kwargs={"started_at": None},
    )


async def _latest_fragment_tx_by_transaction(
        transaction_ids: list[UUID]
) -> dict[UUID, FragmentTransaction]:
    """
    Returns the latest Fragment order for each transaction. Usually, one order
    exists. If a duplicate stars transfer occurs, multiple orders can exist.
    Then, the system uses the newest order.
    """
    query = (
        FragmentTransaction.objects
        .filter(id_from_payment_api__in=transaction_ids)
        .order_by("created_at")
    )
    return {row.id_from_payment_api: row async for row in query}


async def _poll_unfinished_fragment_orders() -> str:
    from core.integrations.fragment.client import FragmentClient
    from core.integrations.webhook_utils import ServicesNames
    from core.ioc import get_container
    from core.services.redis_service import get_lock_latest_status, redis_client, sync_acquire_lock

    now = timezone.now()
    max_age = timedelta(hours=settings.FRAGMENT_POLL_MAX_AGE_HOURS)
    doubt_after = timedelta(minutes=settings.FRAGMENT_POLL_DOUBT_AFTER_MINUTES)

    query = (
        Transaction.objects
        .filter(status__in=WAITING_STATUSES, created_at__gte=now - max_age)
        .order_by("created_at")
    )[:_MAX_PER_TICK]
    waiting = [t async for t in query]
    if not waiting:
        return "no unfinished fragment orders"

    fragment_txs = await _latest_fragment_tx_by_transaction([t.id for t in waiting])
    client = await get_container().get(FragmentClient)

    checked = 0
    advanced = 0
    for txn in waiting:
        fragment_tx = fragment_txs.get(txn.id)
        if fragment_tx is None:
            # The Fragment order does not exist.
            continue

        if redis_client.exists(f"{_ACTED_PREFIX}{txn.id}"):
            continue

        checked += 1
        try:
            order = await client.get_order(fragment_tx.fragment_id, timeout=10.0, connect=5.0)
            if order is None:
                continue

            raw_status = str(order.get("status", ""))
            if not raw_status:
                continue

            internal_status = decide_next_status(
                raw_status, timezone.now() - txn.created_at, doubt_after
            )

            if internal_status is None:
                if raw_status != fragment_tx.status:
                    push_fragment_status(txn.id, fragment_tx.fragment_id, raw_status, None)
                continue

            if internal_status == txn.status:
                continue

            # The system uses the same lock as the webhook to prevent data conflicts
            # if the webhook arrives at the same time.
            # Sync client on purpose: async_to_sync runs every task on a new event loop, and the global async
            # Redis client stays bound to the loop of its first use ("Event loop is closed" from the second run).
            lock = sync_acquire_lock(
                get_lock_latest_status(ServicesNames.FRAGMENT, txn.id),
                timeout=45.0,
                blocking=False, blocking_timeout=0.0
            )
            if lock is None:
                continue

            try:
                push_fragment_status(txn.id, fragment_tx.fragment_id, raw_status, internal_status)
                _ = redis_client.set(f"{_ACTED_PREFIX}{txn.id}", "1", ex=_ACTED_TTL)
                advanced += 1
            finally:
                try:
                    lock.release()
                except Exception as exc:
                    logger.warning(f"Fragment poll: lock release failed for {txn.id}: {exc}")

        except Exception as exc:
            logger.warning(f"Fragment poll: {txn.id} -> {exc.__class__.__name__}: {exc}")

    return f"Fragment poll: checked {checked}, advanced {advanced}"


@shared_task(bind=True, ignore_result=True)
def poll_unfinished_fragment_orders_task(self: Task[P, R], *, started_at: float | None = None) -> str:
    from core.services.redis_service import sync_acquire_lock

    _ = started_at or time.time()

    lock = sync_acquire_lock(_LOCK_NAME, timeout=120.0, blocking=False, blocking_timeout=0.0)
    if lock is None:
        return "Fragment poll already running"

    try:
        return async_to_sync(_poll_unfinished_fragment_orders)()
    finally:
        try:
            lock.release()
        except Exception as exc:
            logger.warning(f"Fragment poll: lock release failed: {exc}")
