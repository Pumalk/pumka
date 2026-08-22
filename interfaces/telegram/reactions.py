"""
interfaces/telegram/reactions.py — реакции на сообщения и определение старых.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict
from aiogram import types
from aiogram.types import Message, ReactionTypeEmoji
from aiogram.dispatcher.middlewares.base import BaseMiddleware

from .queue_users import user_workers

logger = logging.getLogger("pumka.system")

# Порог "старого" сообщения (секунды)
OLD_MESSAGE_THRESHOLD = 300


class ReactionMiddleware(BaseMiddleware):
    """
    Middleware для установки реакций и определения старых сообщений.
    """

    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)
        message = event
        user_id = message.from_user.id
        # Проверяем возраст сообщения
        now = datetime.now(timezone.utc)
        message_time = message.date.replace(tzinfo=timezone.utc)
        age_seconds = (now - message_time).total_seconds()
        is_old = age_seconds > OLD_MESSAGE_THRESHOLD
        data["is_old_message"] = is_old
        data["message_age_seconds"] = age_seconds
        # Устанавливаем начальную реакцию
        if is_old:
            # Старое сообщение — получил с опозданием
            await self._set_reaction(message, "😱")
        else:
            # Новое сообщение — начинаю думать
            await self._set_reaction(message, "🤔")
        return await handler(event, data)

    async def _set_reaction(self, message: Message, emoji: str):
        """Безопасно устанавливает реакцию с небольшой задержкой."""
        try:
            await asyncio.sleep(0.3)  # Задержка чтобы Telegram успел обработать
            await message.bot.set_message_reaction(
                chat_id=message.chat.id,
                message_id=message.message_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)],
                is_big=False,
            )
        except Exception as e:
            logger.warning(f"Не удалось установить реакцию {emoji}: {e}")
