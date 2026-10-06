from django.conf import settings
from functools import wraps
from telegram import ChatMember, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes
from tenacity import retry
from typing import Literal

from bot.callbacks import SubscriptionCallback, create_callback
from bot.context import get_view_context
from bot.enums import BackDestination
from bot.renderers.base import delete_message, send_new_message
from bot.states import BotConversationState
from bot.utils.active_conversation import autosave_active_conversation
from bot.utils.type_aliases import UpdateWithContextHandler
from core.domain.tenacity_utils import TelegramRetryConfig

_retry_config = TelegramRetryConfig().asdict


@retry(**_retry_config)
async def get_chat_member(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> ChatMember:
    return await context.bot.get_chat_member(chat_id=settings.TELEGRAM_CHANNEL_ID,
                                             user_id=user_id)  # pyright: ignore[reportAny]


async def is_user_subscribed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id

    try:
        member = await get_chat_member(context, user_id)
        if member.status not in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
            return True

    except TelegramError as exc:
        if not isinstance(exc, Forbidden) and type(exc) is not TelegramError:
            raise exc

    return False


def require_subscription(back_destination: BackDestination, /):
    def decorator[**P](func: UpdateWithContextHandler[P, BotConversationState | int]):
        @wraps(func)
        async def wrapper(
                update: Update, context: ContextTypes.DEFAULT_TYPE,
                *args: P.args, **kwargs: P.kwargs
        ) -> BotConversationState | int | Literal[BotConversationState.NOT_SUBSCRIBED]:
            if await is_user_subscribed(update, context):
                return await func(update, context, *args, **kwargs)

            tg_user = update.effective_user
            if tg_user is None:
                raise RuntimeError("Во время проверки подписки на канал отсутствует объект User")

            keyboard = [
                [InlineKeyboardButton(
                    "🔮 Перейти в канал", url=settings.TELEGRAM_CHANNEL_LINK  # pyright: ignore[reportAny]
                )],
                [InlineKeyboardButton(
                    "✅ Я подписался(-ась)",
                    callback_data=await create_callback(tg_user.id, SubscriptionCallback(back_destination))
                )]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            text =(
                f"❌ <b>Ошибка! Чтобы пользоваться ботом, подпишись на Telegram-Канал!</b>\n\n"
                f"Будем всегда держать тебя в курсе! ;)"
            )

            ctx = get_view_context(context)
            _ = await delete_message(ctx.active_conversation)

            _ = await (autosave_active_conversation(send_new_message))(
                update, context,
                text, reply_markup, photo_name=None
            )
            return BotConversationState.NOT_SUBSCRIBED
        return wrapper
    return decorator
