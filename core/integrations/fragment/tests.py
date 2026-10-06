"""Отправка звёзд во Fragment, безопасные повторы, неизвестный результат и откат статусов."""
import asyncio
import httpx
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import core.integrations.fragment.webhook_workflow as fragment_workflow
import core.integrations.platega.tasks as payment_tasks
from core.domain.enums import TransactionStatus
from core.integrations.fragment.client import FragmentClient
from core.integrations.fragment.enums import FragmentStatus
from core.integrations.fragment.errors import (
    FragmentAPIError, FragmentAPINetworkError, FragmentAPIUnknownResultError
)


def _client(http: MagicMock, settings) -> FragmentClient:
    settings.IS_DEBUG = False
    settings.FRAGMENT_API_URL = "https://api.fragment-api.com/v1/"
    return FragmentClient(http, MagicMock(get_fragment_api_jwt_token=AsyncMock(return_value="jwt")))


def _send(client: FragmentClient) -> None:
    _ = asyncio.run(
        client._send_stars_request({"username": "someone", "quantity": 50}))  # pyright: ignore[reportPrivateUsage]


# Классификация ошибок POST /order/stars/

@pytest.mark.parametrize("outcome", [
    httpx.Response(502), httpx.Response(500),
    httpx.ReadTimeout("read"), httpx.RemoteProtocolError("dropped"),
], ids=["502", "500", "read-timeout", "connection-dropped"])
def test_send_without_definite_answer_is_unknown_result(outcome, settings):
    http = MagicMock()
    if isinstance(outcome, httpx.Response):
        http.post.return_value = outcome
    else:
        http.post.side_effect = outcome

    with pytest.raises(FragmentAPIUnknownResultError):
        _send(_client(http, settings))


def test_connect_failure_is_safe_to_retry(settings):
    http = MagicMock()
    http.post.side_effect = httpx.ConnectError("refused")
    with pytest.raises(FragmentAPINetworkError):
        _send(_client(http, settings))


def test_rejected_send_is_a_definite_failure(settings):
    http = MagicMock()
    http.post.return_value = httpx.Response(400, json={"error": "bad username"})
    with pytest.raises(FragmentAPIError) as exc_info:
        _send(_client(http, settings))
    assert not isinstance(exc_info.value, FragmentAPIUnknownResultError)


def test_read_only_request_timeout_is_not_unknown_result(settings):
    http = MagicMock()
    http.get.side_effect = httpx.ReadTimeout("read")
    with pytest.raises(FragmentAPIError) as exc_info:
        _ = asyncio.run(_client(http, settings).get_order(uuid4()))
    assert not isinstance(exc_info.value, FragmentAPIUnknownResultError)


# send_stars_workflow

@pytest.mark.parametrize("error, expected_status", [
    (FragmentAPIUnknownResultError("timeout after POST"), TransactionStatus.IN_DOUBT),
    (FragmentAPIError("400 bad username"), TransactionStatus.FAILED),
])
def test_send_stars_failure_status(error, expected_status, monkeypatch):
    transaction_id = uuid4()
    saved = MagicMock()
    monkeypatch.setattr(payment_tasks, "safe_get_transaction_with_retries",
                        AsyncMock(return_value=SimpleNamespace(status=TransactionStatus.SENDING)))
    monkeypatch.setattr(payment_tasks, "create_fragment_transaction_if_not_sent_with_retries",
                        AsyncMock(side_effect=error))
    monkeypatch.setattr(payment_tasks, "sync_save_status_by_key", saved)
    monkeypatch.setattr(payment_tasks, "update_transaction_status_task", MagicMock())
    monkeypatch.setattr(payment_tasks, "update_transaction_payload_task", MagicMock())

    _ = asyncio.run(payment_tasks.send_stars_workflow(
        MagicMock(request=SimpleNamespace(kwargs={})), transaction_id, None, "", started_at=0.0
    ))

    saved.assert_called_once()
    assert saved.call_args.args[2] == expected_status
    payment_tasks.update_transaction_status_task.apply_async.assert_called_once()


# Запоздавший статус не должен откатывать финальный

def _run_fragment_workflow(monkeypatch, current: FragmentStatus, new: FragmentStatus) -> AsyncMock:
    set_status = AsyncMock(return_value=True)
    monkeypatch.setattr(fragment_workflow, "safe_get_fragment_tx_with_retries",
                        AsyncMock(return_value=SimpleNamespace(status=current)))
    monkeypatch.setattr(fragment_workflow, "safe_set_status_for_fragment_tx_id_with_retries", set_status)
    _ = asyncio.run(fragment_workflow.update_fragment_transaction_workflow(
        MagicMock(request=SimpleNamespace(kwargs={})), uuid4(), uuid4(), new, started_at=0.0
    ))
    return set_status


def test_late_created_status_does_not_regress_completed_order(monkeypatch):
    _run_fragment_workflow(monkeypatch, FragmentStatus.COMPLETED, FragmentStatus.CREATED).assert_not_awaited()


def test_final_status_still_applies_to_pending_order(monkeypatch):
    _run_fragment_workflow(monkeypatch, FragmentStatus.PENDING, FragmentStatus.COMPLETED).assert_awaited_once()
