import logging
from dishka import FromDishka
from django.conf import settings
from telegram import Update, ChatMemberUpdated, Chat, ChatMember
from telegram.ext import ApplicationHandlerStop, ContextTypes
from typing import overload

from core.ioc import inject
from core.repositories.utils import db_action_with_tenacity
from core.services.user import UserService

logger = logging.getLogger(__name__)


async def enforce_private_chats_only_or_admin_chat(update: object, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not isinstance(update, Update):
        return

    chat = update.effective_chat

    if not chat:
        return

    if chat.type == chat.PRIVATE:
        return

    if update.my_chat_member:
        chat_member = update.my_chat_member.new_chat_member
        status = chat_member.status
        if status in [chat_member.LEFT, chat_member.BANNED]:
            raise ApplicationHandlerStop()

    chat_id = chat.id
    if chat_id != settings.TELEGRAM_ADMIN_CHAT_ID and chat_id != settings.TELEGRAM_CHANNEL_ID:  # pyright: ignore[reportAny]
        try:
            logger.info(f"Обнаружен несанкционированный чат {chat.title} ({chat_id}). Попытка выхода...")
            if await chat.leave():
                logger.info("Выход из чата прошёл успешно!")
            else:
                logger.error(f"Не удалось выйти из чата {chat_id} по неизвестной причине (возможно сбой сети)")

        except Exception as exc:
            if "not a member" not in str(exc):
                logger.error(f"Не удалось выйти из чата {chat_id}: {exc}")

    raise ApplicationHandlerStop()


@overload
async def track_chat_member_update(  # noqa  # pyright: ignore[reportInconsistentOverload]
        update: Update, _: ContextTypes.DEFAULT_TYPE
) -> None: ...

@inject
async def track_chat_member_update(
        update: Update, _: ContextTypes.DEFAULT_TYPE,
        *,
        user_service: FromDishka[UserService]  # noqa
) -> None:
    my_chat_member: ChatMemberUpdated | None = update.my_chat_member
    chat: Chat = my_chat_member.chat
    if chat.type != chat.PRIVATE:
        return

    user_id: int = my_chat_member.from_user.id
    new_chat_member: ChatMember = my_chat_member.new_chat_member
    new_status: str = new_chat_member.status

    if new_status in (new_chat_member.BANNED, new_chat_member.LEFT):
        logger.info(f"Бот: пользователь {user_id} ЗАБЛОКИРОВАЛ бота")
        __ = await db_action_with_tenacity(user_service.update_is_active, user_id, False)
        raise ApplicationHandlerStop()

    elif new_status in (new_chat_member.MEMBER, new_chat_member.ADMINISTRATOR, new_chat_member.OWNER):
        logger.info(f"Бот: пользователь {user_id} РАЗБЛОКИРОВАЛ бота")
        __ = await db_action_with_tenacity(user_service.update_is_active, user_id, True)
