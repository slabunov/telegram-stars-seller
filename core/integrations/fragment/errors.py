from typing import final


class FragmentAPIError(Exception):
    """Базовая ошибка клиента Fragment API."""


class FragmentAPINetworkError(FragmentAPIError):
    """Ошибка сети, когда запрос точно НЕ был отправлен."""


class FragmentAPIUnknownResultError(FragmentAPIError):
    """Запрос на изменение был отправлен, но ответа нет (тайм-аут, 5xx)."""


@final
class FragmentAPITooManyRequests(Exception):
    """Ошибка для HTTP со статусом 429."""
    def __init__(self, retry_after: int | float | None = None, message: str | None = None) -> None:
        self.retry_after = retry_after
        self.message = message
        super().__init__(self.message)


@final
class FragmentAPITemporaryError(Exception):
    def __init__(self, technical_message: str, bot_message: str) -> None:
        self.technical_message = technical_message
        self.bot_message = bot_message
        super().__init__(self.technical_message)


@final
class FragmentAPINotEnoughBalanceError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(self.message)
