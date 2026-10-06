import asyncio
import socket
import warnings
from collections.abc import Awaitable
from concurrent.futures import ThreadPoolExecutor
from django.conf import settings
from django.core.management.base import BaseCommand
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    ChatMemberHandler,
    TypeHandler,
    # PicklePersistence, PersistenceInput
)
from telegram.request import HTTPXRequest
from telegram.warnings import PTBUserWarning
from typing import final, override

from bot.handlers.error import error_handler
from bot.middlewares.chat import enforce_private_chats_only_or_admin_chat, track_chat_member_update
from bot.middlewares.user import register_user_middleware
from bot.router import get_conversation_handler, get_debug_handlers
from bot.utils.type_aliases import DefaultApplication
from core.ioc import close_container
from core.services.redis_service import close_async_redis_client, listen_redis_for_broadcasts


@final
class Command(BaseCommand):
    help = "Запуск Telegram бота"

    request_config = HTTPXRequest(
        http_version="1.1",
        connection_pool_size=20,    # Кол-во открытых соединений
        connect_timeout=20.0,       # Время на установку соединения
        read_timeout=25.0,          # Время ожидания ответа от серверов Telegram
        write_timeout=25.0,         # Время на отправку данных (обычный текст)
        media_write_timeout=60.0,   # Время на загрузку тяжелых файлов/медиа
        socket_options=[
            (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
            (socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10),  # Пинг после 10 сек простоя (больше 10 из-за beget нельзя)
            (socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 3),  # Интервал повтора пинга
            (socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)     # Кол-во попыток
        ]
    )

    async def post_init(self, application: DefaultApplication) -> None:
        commands = [BotCommand("start", "Сделать новый заказ")]
        if settings.IS_DEBUG:  # pyright: ignore[reportAny]
            user_warning = "Режим отладки - если ты обычный пользователь, сообщи об ошибке в тех. поддержку"
            commands.append(BotCommand("balance", user_warning))
            commands.append(BotCommand("balance_debug", user_warning))
            commands.append(BotCommand("prices", user_warning))
            commands.append(BotCommand("prices_debug", user_warning))
        _ = await application.bot.set_my_commands(commands)

        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=50))
        self.stdout.write("Бот: ThreadPoolExecutor расширен до 50 потоков.")

        self.stdout.write("Бот: создаём прослушиватель публикаций Redis для рассылок...")
        application.bot_data["stop_redis"] = False
        application.bot_data["broadcast_listener"] = asyncio.create_task(listen_redis_for_broadcasts(application))
        self.stdout.write("Бот: прослушиватель публикаций Redis для рассылок создан!")

    async def post_stop(self, application: DefaultApplication) -> None:
        self.stdout.write("Bot is stopping...")

        self.stdout.write("Бот: останавливаем прослушиватель публикаций Redis для рассылок...")
        application.bot_data["stop_redis"] = True
        task = application.bot_data.get("broadcast_listener", None)
        if task is not None and isinstance(task, Awaitable):
            await task
        self.stdout.write("Бот: прослушиватель публикаций Redis для рассылок остановлен!")

        self.stdout.write("Closing DI container in bot...")
        try:
            await close_container()
            self.stdout.write("DI container in bot closed successfully!")
        except Exception as err:
            self.stderr.write(f"Error while closing DI container in bot: {err}")

        self.stdout.write("Closing async redis client in bot...")
        try:
            await close_async_redis_client()
            self.stdout.write("Async redis client in bot closed successfully!")
        except Exception as err:
            self.stderr.write(f"Error while closing async redis client in bot: {err}")

    @override
    def handle(self, *args: object, **options: object):
        # Есть предупреждение о per_message=False, но оно возникает в любом случае, т. е. и при True;
        # от него зависит поведение того, как выбирается состояние для ConversationHandler;
        # при False оно одно для всех сообщений; при True то сообщение, которое генерирует функция начала Conversation,
        # будет сохранено по его id, и конкретно для него будет изменяться состояние, т.е. можно будет иметь несколько
        # Conversation со своими состояниями; в нашем случае нельзя использовать per_message=True, т.к. иногда
        # начальное сообщение от Conversation необходимо удалить и сделать новое - в таком случае id не обновится
        # для хэндлера
        warnings.filterwarnings("ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

        self.stdout.write("Бот запускается...")

        # data_dir = Path(settings.BASE_DIR) / "bot" / "data"
        # data_dir.mkdir(parents=True, exist_ok=True)
        # filepath = data_dir / "bot_persistence.pickle"
        #
        # persistence = PicklePersistence(
        #     filepath=filepath,
        #     store_data=PersistenceInput(
        #         bot_data=False
        #     ),
        #     update_interval=30
        # )

        # .persistence(persistence) TODO: продумать персистентность
        application = (
            ApplicationBuilder()  # pyright: ignore[reportUnknownMemberType]
            .token(settings.TELEGRAM_BOT_TOKEN)  # pyright: ignore[reportAny]
            .request(self.request_config)
            .post_init(self.post_init)
            .post_stop(self.post_stop)
            .build()
        )

        application.add_error_handler(error_handler)  # noqa

        application.add_handler(
            TypeHandler(Update, enforce_private_chats_only_or_admin_chat), group=-3
        )
        application.add_handler(
            TypeHandler(Update, register_user_middleware), group=-2
        )
        application.add_handler(ChatMemberHandler(track_chat_member_update), group=-1)  # noqa
        application.add_handler(get_conversation_handler())

        if settings.IS_DEBUG:  # pyright: ignore[reportAny]
            handlers = get_debug_handlers()
            for handler in handlers:
                application.add_handler(handler)

        self.stdout.write("Бот настроен! Пытаемся подключиться к серверу Telegram...")
        application.run_polling()
