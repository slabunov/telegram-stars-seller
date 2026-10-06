import logging
from django.apps import AppConfig
from django.conf import settings
from django.db.models import Transform, JSONField
from typing import final, override

logger = logging.getLogger(__name__)


@final
class CoreConfig(AppConfig):
    name = 'core'

    @override
    def ready(self) -> None:
        logger.info(f"Application environment: {settings.APP_ENV}")
        if settings.DEBUG:  # pyright: ignore[reportAny]
            logger.info("Django debug mode enabled.")
        # logger.info(f"Telegram bot configuration: {settings.APP_ENV}")
        for message in settings.CONFIG_LOG_MESSAGES:  # pyright: ignore[reportAny]
            logger.info(message)

@final
class SQLiteJsonNormalizer(Transform):
    lookup_name = "normalize"

    # Когда передаётся только сам JSON, SQLite ре-форматирует его, то есть отсортирует ключи в алфавитном порядке
    # (подойдёт не только эта SQLite функция, но неважно, какая)
    function = "json_remove"


_ = JSONField.register_lookup(SQLiteJsonNormalizer)
