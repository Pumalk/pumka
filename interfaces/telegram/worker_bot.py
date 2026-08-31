"""
interfaces/telegram/worker_bot.py — общие функции для отправки сообщений из воркеров.
Воркеры работают в отдельных процессах, поэтому создают своего бота через create_bot_auto.
"""
import asyncio
import logging
from typing import Optional
from aiogram.types import ReactionTypeEmoji

from core.config import load_config
from interfaces.telegram.proxy import create_bot_auto, BotTransport

logger = logging.getLogger("pumka.system")


async def _get_bot_async() -> Optional[BotTransport]:
    """Асинхронно создаёт бота через create_bot_auto."""
    config = load_config()
    transport = await create_bot_auto(config)
    if transport is None:
        logger.error("worker_bot: не удалось создать бота")
        return None
    return transport


def get_bot() -> Optional[BotTransport]:
    """Синхронная обёртка для получения бота (для использования в воркерах)."""
    return asyncio.run(_get_bot_async())


async def _send_message_async(chat_id: int, text: str, reply_to_message_id: Optional[int] = None) -> bool:
    """Асинхронно отправляет сообщение в чат."""
    transport = await _get_bot_async()
    if transport is None:
        return False
    try:
        bot = transport.bot
        if reply_to_message_id:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=reply_to_message_id,
            )
        else:
            await bot.send_message(chat_id=chat_id, text=text)
        logger.info(f"worker_bot: сообщение отправлено в чат {chat_id}")
        return True
    except Exception as e:
        logger.error(f"worker_bot: ошибка отправки сообщения в чат {chat_id}: {e}")
        return False
    finally:
        await transport.bot.session.close()


def send_message(chat_id: int, text: str, reply_to_message_id: Optional[int] = None) -> bool:
    """Синхронная обёртка для отправки сообщения (для использования в воркерах)."""
    return asyncio.run(_send_message_async(chat_id, text, reply_to_message_id))


async def _set_reaction_async(chat_id: int, message_id: int, emoji: str) -> bool:
    """Асинхронно устанавливает реакцию на сообщение."""
    transport = await _get_bot_async()
    if transport is None:
        return False
    try:
        bot = transport.bot
        await asyncio.sleep(0.3)  # Задержка, чтобы Telegram успел обработать
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
            is_big=False,
        )
        logger.info(f"worker_bot: реакция {emoji} установлена на сообщение {message_id}")
        return True
    except Exception as e:
        logger.warning(f"worker_bot: не удалось установить реакцию {emoji}: {e}")
        return False
    finally:
        await transport.bot.session.close()


def set_reaction(chat_id: int, message_id: int, emoji: str) -> bool:
    """Синхронная обёртка для установки реакции (для использования в воркерах)."""
    return asyncio.run(_set_reaction_async(chat_id, message_id, emoji))
