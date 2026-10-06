"""Проверки решения о статусе для опроса заказов Fragment."""

import pytest
from datetime import timedelta
from uuid import uuid4

from core.domain.enums import TransactionStatus, is_change_status_allowed
from core.integrations.fragment.enums import FragmentStatus
from core.tasks.periodic.fragment_orders import WAITING_STATUSES, decide_next_status


_DOUBT_AFTER = timedelta(minutes=30)
_FRESH = timedelta(minutes=1)
_STALE = timedelta(hours=2)


def test_completed_order_closes_transaction():
    assert decide_next_status(FragmentStatus.COMPLETED, _FRESH, _DOUBT_AFTER) == TransactionStatus.SUCCESS
    assert decide_next_status(FragmentStatus.COMPLETED, _STALE, _DOUBT_AFTER) == TransactionStatus.SUCCESS


def test_failed_order_closes_transaction():
    assert decide_next_status(FragmentStatus.FAILED, _FRESH, _DOUBT_AFTER) == TransactionStatus.FAILED


def test_fresh_order_in_progress_is_left_alone():
    # пока заказ свежий, промежуточные статусы не должны трогать транзакцию
    for status in (FragmentStatus.CREATED, FragmentStatus.PENDING, FragmentStatus.BLOCKCHAIN_SENT):
        assert decide_next_status(status, _FRESH, _DOUBT_AFTER) is None


def test_stuck_order_goes_to_doubt():
    for status in (FragmentStatus.CREATED, FragmentStatus.PENDING, FragmentStatus.BLOCKCHAIN_SENT):
        assert decide_next_status(status, _STALE, _DOUBT_AFTER) == TransactionStatus.IN_DOUBT


def test_unknown_status_is_treated_as_in_progress_then_doubt():
    assert decide_next_status("WAT", _FRESH, _DOUBT_AFTER) is None
    assert decide_next_status("WAT", _STALE, _DOUBT_AFTER) == TransactionStatus.IN_DOUBT


def test_every_decision_is_an_allowed_transition_from_waiting_statuses():
    ages = (_FRESH, _STALE)
    for current_status in WAITING_STATUSES:
        for raw_status in FragmentStatus.all_enums():
            for age in ages:
                new_status = decide_next_status(raw_status, age, _DOUBT_AFTER)
                if new_status is None or new_status == current_status:
                    continue
                assert is_change_status_allowed(current_status, new_status), (
                    f"{current_status} -> {new_status} (fragment {raw_status}) запрещён матрицей переходов"
                )


def test_waiting_statuses_are_not_final_for_the_user():
    # транзакция в этих статусах ещё висит у пользователя как "Заказ обрабатывается..."
    assert TransactionStatus.SEND_CREATED in WAITING_STATUSES
    assert TransactionStatus.SUCCESS not in WAITING_STATUSES
    assert TransactionStatus.FAILED not in WAITING_STATUSES



@pytest.mark.django_db
def test_poller_advances_stuck_order_on_every_run_in_one_process(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from asgiref.sync import async_to_sync
    import core.ioc
    import core.services.redis_service as redis_service
    import core.tasks.periodic.fragment_orders as poller
    from core.models import FragmentTransaction, TelegramUser, Transaction

    user = TelegramUser.objects.create(telegram_id=1, username="buyer")
    txn = Transaction.objects.create(
        id=uuid4(), telegram_user=user, amount_fiat=100, amount_stars=50, status=TransactionStatus.SEND_CREATED
    )
    fragment_id = uuid4()
    FragmentTransaction.objects.create(fragment_id=fragment_id, id_from_payment_api=txn.id, status=FragmentStatus.CREATED)

    client = MagicMock(get_order=AsyncMock(return_value={"status": FragmentStatus.COMPLETED}))
    monkeypatch.setattr(core.ioc, "get_container", lambda: MagicMock(get=AsyncMock(return_value=client)))
    monkeypatch.setattr(redis_service, "redis_client", MagicMock(exists=MagicMock(return_value=0)))
    monkeypatch.setattr(redis_service, "sync_acquire_lock", MagicMock(return_value=MagicMock()))
    pushed = MagicMock()
    monkeypatch.setattr(poller, "push_fragment_status", pushed)

    for _ in range(2):
        _ = async_to_sync(poller._poll_unfinished_fragment_orders)()  # pyright: ignore[reportPrivateUsage]

    assert pushed.call_count == 2
    pushed.assert_called_with(txn.id, fragment_id, FragmentStatus.COMPLETED, str(TransactionStatus.SUCCESS))
