class PayHotAPIError(Exception):
    """Базовая ошибка при работе с API PayHot."""


class PayHotAPINetworkError(PayHotAPIError):
    """Ошибка сети, когда запрос был точно НЕ отправлен."""
