import asyncio
import logging
from django.conf import settings
from django.db.models import QuerySet
from io import BufferedReader
from mimetypes import guess_type
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from tenacity import retry
from typing import TypedDict, NotRequired

from core.domain.tenacity_utils import TelegramRetryConfig
from core.domain.type_aliases import AsyncCallable
from core.models import Broadcast, TelegramUser
from core.repositories.utils import db_action_or_exception_with_tenacity, db_action_with_tenacity

logger = logging.getLogger(__name__)

_parse_mode = ParseMode.HTML

_retry_config = TelegramRetryConfig().asdict


class _PhotoKwargs(TypedDict):
    chat_id: int
    parse_mode: ParseMode
    photo: str | BufferedReader
    caption: str
    reply_markup: InlineKeyboardMarkup | None
    message_thread_id: NotRequired[int | None]


class _VideoKwargs(TypedDict):
    chat_id: int
    parse_mode: ParseMode
    video: str | BufferedReader
    caption: str
    reply_markup: InlineKeyboardMarkup | None
    message_thread_id: NotRequired[int | None]


class _DocumentKwargs(TypedDict):
    chat_id: int
    parse_mode: ParseMode
    document: str | BufferedReader
    caption: str
    reply_markup: InlineKeyboardMarkup | None
    message_thread_id: NotRequired[int | None]


class _TextKwargs(TypedDict):
    chat_id: int
    parse_mode: ParseMode
    text: str
    reply_markup: InlineKeyboardMarkup | None
    message_thread_id: NotRequired[int | None]


class _BroadcastResult(TypedDict):
    success: int
    failed: int


@retry(**_retry_config)
async def _retry_action[**P,R](
        bot_action: AsyncCallable[P,R],
        *args: P.args, **kwargs: P.kwargs
) -> R:
    return await bot_action(*args, **kwargs)


def _build_keyboard(
        button_texts: list[list[str]] | None,
        button_urls: list[list[str]] | None
) -> InlineKeyboardMarkup | None:
    if not button_texts or not button_urls:
        return None

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(text=t, url=u) for t, u in zip(texts, urls)]
        for texts, urls in zip(button_texts, button_urls)
    ])


async def _send_preview_and_get_file_id(
        bot: Bot,
        chat_id: int,
        text: str,
        media_path: str | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
        thread_id: int | None = None
) -> str | None:
    if not media_path:
        text_kwargs = _TextKwargs(
            chat_id=chat_id, parse_mode=_parse_mode,
            text=text, reply_markup=reply_markup, message_thread_id=thread_id
        )
        _ = await _retry_action(bot.send_message, **text_kwargs)
        return None

    mime_type, _ = guess_type(media_path)
    with open(media_path, "rb") as f:
        if mime_type and mime_type.startswith("image"):
            photo_kwargs = _PhotoKwargs(
                chat_id=chat_id, parse_mode=_parse_mode,
                photo=f, caption=text, reply_markup=reply_markup, message_thread_id=thread_id
            )
            msg = await _retry_action(bot.send_photo, **photo_kwargs)
            return msg.photo[-1].file_id

        elif mime_type and mime_type.startswith("video"):
            video_kwargs = _VideoKwargs(
                chat_id=chat_id, parse_mode=_parse_mode,
                video=f, caption=text, reply_markup=reply_markup, message_thread_id=thread_id
            )
            msg = await _retry_action(bot.send_video, **video_kwargs)
            return msg.video.file_id

        else:
            document_kwargs = _DocumentKwargs(
                chat_id=chat_id, parse_mode=_parse_mode,
                document=f, caption=text, reply_markup=reply_markup, message_thread_id=thread_id
            )
            msg = await _retry_action(bot.send_document, **document_kwargs)
            return msg.document.file_id


async def process_preview(bot: Bot, broadcast_id: int) -> None:
    broadcast = await Broadcast.objects.aget(id=broadcast_id)

    broadcast_name = f'"{broadcast.name}"'
    if not broadcast_name:
        broadcast_name = broadcast_id

    reply_markup = _build_keyboard(broadcast.button_texts, broadcast.button_urls)
    media_path = broadcast.media.path if broadcast.media else None

    try:
        file_id = await _send_preview_and_get_file_id(
            bot=bot,
            chat_id=settings.TELEGRAM_ADMIN_CHAT_ID,  # pyright: ignore[reportAny]
            text=broadcast.text,
            media_path=media_path,
            reply_markup=reply_markup,
            thread_id=settings.TELEGRAM_ADMIN_BROADCAST_TOPIC_ID  # pyright: ignore[reportAny]
        )

        broadcast.telegram_file_id = file_id
        broadcast.preview_sent = True

        await db_action_with_tenacity(broadcast.asave, update_fields=["telegram_file_id", "preview_sent"])
        logger.info(f"broadcast {broadcast_name} preview send success")

    except Exception as exc:
        logger.exception(f"Ошибка отправки предпросмотра рассылки {broadcast_name}: {exc}", exc_info=False)


async def _send_message_with_retries[**P,R](
        bot_action: AsyncCallable[P,R], broadcast_name: str | int, user: TelegramUser,
        /,
        *args: P.args, **kwargs: P.kwargs
) -> bool:
    try:
        _ = await _retry_action(bot_action, *args, **kwargs)
        return True

    except Exception as exc:
        logger.exception(
            f"Не удалось отправить {broadcast_name} юзеру {user.telegram_id}: {exc}", exc_info=False
        )
        if isinstance(exc, BadRequest) and "not found" in str(exc) or isinstance(exc, Forbidden):
            user.is_active = False
            result = await db_action_or_exception_with_tenacity(
                user.asave, update_fields=["is_active", "updated_at"]
            )
            if isinstance(result, Exception):
                logger.exception(str(result), exc_info=False)

    finally:
        await asyncio.sleep(0.05)

    return False


async def _mass_send_photo(
        bot: Bot, broadcast_name: str | int,
        users_qs: QuerySet[TelegramUser],
        file_id: str, text: str, reply_markup: InlineKeyboardMarkup | None
) -> _BroadcastResult:
    success = failed = 0

    async for user in users_qs:
        photo_kwargs = _PhotoKwargs(
            chat_id=user.telegram_id, parse_mode=_parse_mode,
            photo=file_id, caption=text, reply_markup=reply_markup
        )
        is_success = await _send_message_with_retries(bot.send_photo, broadcast_name, user, **photo_kwargs)
        if is_success:
            success += 1
        else:
            failed += 1

    return _BroadcastResult(success=success, failed=failed)


async def _mass_send_video(
        bot: Bot, broadcast_name: str | int,
        users_qs: QuerySet[TelegramUser],
        file_id: str, text: str, reply_markup: InlineKeyboardMarkup | None
) -> _BroadcastResult:
    success = failed = 0

    async for user in users_qs:
        video_kwargs = _VideoKwargs(
            chat_id=user.telegram_id, parse_mode=_parse_mode,
            video=file_id, caption=text, reply_markup=reply_markup
        )
        is_success = await _send_message_with_retries(bot.send_video, broadcast_name, user, **video_kwargs)
        if is_success:
            success += 1
        else:
            failed += 1

    return _BroadcastResult(success=success, failed=failed)


async def _mass_send_document(
        bot: Bot, broadcast_name: str | int,
        users_qs: QuerySet[TelegramUser],
        file_id: str, text: str, reply_markup: InlineKeyboardMarkup | None
) -> _BroadcastResult:
    success = failed = 0

    async for user in users_qs:
        document_kwargs = _DocumentKwargs(
            chat_id=user.telegram_id, parse_mode=_parse_mode,
            document=file_id, caption=text, reply_markup=reply_markup
        )
        is_success = await _send_message_with_retries(bot.send_document, broadcast_name, user, **document_kwargs)
        if is_success:
            success += 1
        else:
            failed += 1

    return _BroadcastResult(success=success, failed=failed)


async def _mass_send_text(
        bot: Bot, broadcast_name: str | int,
        users_qs: QuerySet[TelegramUser],
        text: str, reply_markup: InlineKeyboardMarkup | None
) -> _BroadcastResult:
    success = failed = 0

    async for user in users_qs:
        text_kwargs = _TextKwargs(
            chat_id=user.telegram_id, parse_mode=_parse_mode,
            text=text, reply_markup=reply_markup
        )
        is_success = await _send_message_with_retries(bot.send_message, broadcast_name, user, **text_kwargs)
        if is_success:
            success += 1
        else:
            failed += 1

    return _BroadcastResult(success=success, failed=failed)


async def _mass_send(
        bot: Bot, broadcast_name: str | int,
        users_qs: QuerySet[TelegramUser],
        text: str,
        file_id: str | None = None,
        media_path: str | None = None,
        reply_markup: InlineKeyboardMarkup | None = None
) -> _BroadcastResult:
    if file_id and media_path:
        mime_type, _ = guess_type(media_path)

        if mime_type and mime_type.startswith("image"):
            return await _mass_send_photo(bot, broadcast_name, users_qs, file_id, text, reply_markup)

        elif mime_type and mime_type.startswith("video"):
            return await _mass_send_video(bot, broadcast_name, users_qs, file_id, text, reply_markup)

        else:
            return await _mass_send_document(bot, broadcast_name, users_qs, file_id, text, reply_markup)

    return await _mass_send_text(bot, broadcast_name, users_qs, text, reply_markup)


async def process_broadcast(bot: Bot, broadcast_id: int) -> None:
    broadcast = await Broadcast.objects.aget(id=broadcast_id)

    broadcast_name = f'"{broadcast.name}"'
    if not broadcast_name:
        broadcast_name = broadcast_id

    reply_markup = _build_keyboard(broadcast.button_texts, broadcast.button_urls)
    media_path = broadcast.media.path if broadcast.media else None

    users_qs = TelegramUser.objects.filter(is_active=True)

    try:
        broadcast_result = await _mass_send(
            bot=bot, broadcast_name=broadcast_name,
            users_qs=users_qs,
            text=broadcast.text,
            file_id=broadcast.telegram_file_id,
            media_path=media_path,
            reply_markup=reply_markup
        )

        broadcast.is_sent = True
        await db_action_with_tenacity(broadcast.asave, update_fields=["is_sent"])

        logger.info(f"mass broadcast {broadcast_name} -> {broadcast_result}")

    except Exception as exc:
        logger.exception(f"Ошибка отправки массовой рассылки {broadcast_name}: {exc}", exc_info=False)

