"""
interfaces/telegram/middlewares.py — middleware для проверки доступа.
"""
import logging
from typing import Optional, Any, Awaitable, Callable, Dict
from aiogram import types
from aiogram.types import Message
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from core.config import Config

logger = logging.getLogger("pumka.system")
incidents_logger = logging.getLogger("pumka.incidents")


class AccessMiddleware(BaseMiddleware):
    """
    Middleware для проверки, что пользователь имеет доступ к боту.
    Получает config из workflow data Dispatcher'а.
    """
    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """Проверяет, что пользователь разрешён."""
        config: Optional[Config] = data.get("config")
        if not config:
            logger.error("Config не найден в workflow data!")
            return await handler(event, data)
        user = None
        if isinstance(event, Message):
            user = event.from_user
        if user and user.id != config.telegram.allowed_user_id:
            incidents_logger.warning(
                f"Чужой пользователь попытался написать боту: "
                f"user_id={user.id}, username={user.username}"
            )
            return None
        return await handler(event, data)
