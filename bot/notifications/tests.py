"""Повторный запуск рассылки, пока она ещё идёт, не должен отправлять её всем второй раз."""
import asyncio
from unittest.mock import MagicMock

import bot.notifications.broadcast as broadcast


def test_duplicate_broadcast_start_is_ignored_while_running(monkeypatch):
    sends: list[int] = []

    async def slow_broadcast(_bot: object, broadcast_id: int) -> None:
        sends.append(broadcast_id)
        await asyncio.sleep(0.05)

    monkeypatch.setattr(broadcast, "_process_broadcast", slow_broadcast)

    async def double_click() -> None:
        _ = await asyncio.gather(broadcast.process_broadcast(MagicMock(), 7), broadcast.process_broadcast(MagicMock(), 7))

    asyncio.run(double_click())
    assert sends == [7]

    asyncio.run(broadcast.process_broadcast(MagicMock(), 7))
    assert sends == [7, 7]
