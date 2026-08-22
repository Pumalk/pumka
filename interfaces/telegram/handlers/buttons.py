"""
interfaces/telegram/handlers/buttons.py — обработчики кнопок главного меню.
"""
import asyncio
import logging
from typing import Dict, List
from aiogram import Router, F
from aiogram.types import Message, ReactionTypeEmoji, ReplyParameters
from aiogram.utils.chat_action import ChatActionSender
from core.config import Config
from core.health_check import run_health_check
from interfaces.telegram.keyboards import main_menu_keyboard
from interfaces.telegram.prompts import HELP_TEXT
from interfaces.telegram.queue_users import clear_user_queue

logger = logging.getLogger("pumka.system")


async def button_new_chat(
    message: Message,
    config: Config,
    chat_memory: Dict[int, List[Dict[str, str]]],
):
    """Кнопка очистки памяти диалога."""
    user_id = message.from_user.id
    chat_memory.pop(user_id, None)
    # Новый чат = забыть всё, включая неотвеченные вопросы в очереди
    cleared = clear_user_queue(user_id)
    if cleared:
        logger.info(
            f"Новый чат: из очереди убрано сообщений={cleared} для {user_id}"
        )
    logger.info(
        f"Кнопка 'Новый чат' от пользователя {user_id}. Память диалога очищена."
    )
    await message.answer(
        "Начат новый чат. Чем помочь?",
        reply_markup=main_menu_keyboard(),
        reply_parameters=ReplyParameters(message_id=message.message_id),
    )
    # Устанавливаем реакцию "обработано"
    try:
        await asyncio.sleep(0.3)
        await message.bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[ReactionTypeEmoji(emoji="👍")],
            is_big=False,
        )
    except Exception as e:
        logger.warning(f"Не удалось установить реакцию 👍: {e}")


async def button_queue(message: Message, config: Config):
    logger.info(f"Кнопка 'Очередь' от пользователя {message.from_user.id}")
    await message.answer(
        "Очередь задач будет доступна на Этапе 6. Пока пусто.",
        reply_markup=main_menu_keyboard(),
        reply_parameters=ReplyParameters(message_id=message.message_id),
    )
    # Устанавливаем реакцию "обработано"
    try:
        await asyncio.sleep(0.3)
        await message.bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[ReactionTypeEmoji(emoji="👍")],
            is_big=False,
        )
    except Exception as e:
        logger.warning(f"Не удалось установить реакцию 👍: {e}")


async def button_health(message: Message, config: Config):
    logger.info(f"Кнопка 'Здоровье' от пользователя {message.from_user.id}")
    async with ChatActionSender(
        bot=message.bot, chat_id=message.chat.id, action="typing"
    ):
        report = await asyncio.to_thread(run_health_check)
    report_lines = ["🏥 Проверка здоровья:\n"]
    for check in report.oks:
        report_lines.append(f"✅ {check.check}: {check.message}")
    for check in report.errors:
        report_lines.append(f"❌ {check.check}: {check.message}")
    for check in report.warnings:
        report_lines.append(f"⚠️ {check.check}: {check.message}")
    report_text = "\n".join(report_lines)
    await message.answer(
        report_text,
        reply_markup=main_menu_keyboard(),
        reply_parameters=ReplyParameters(message_id=message.message_id),
        parse_mode=None,
    )
    # Устанавливаем реакцию "обработано"
    try:
        await asyncio.sleep(0.3)
        await message.bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[ReactionTypeEmoji(emoji="👍")],
            is_big=False,
        )
    except Exception as e:
        logger.warning(f"Не удалось установить реакцию 👍: {e}")


async def button_help(message: Message, config: Config):
    logger.info(f"Кнопка 'Помощь' от пользователя {message.from_user.id}")
    await message.answer(
        HELP_TEXT,
        reply_markup=main_menu_keyboard(),
        reply_parameters=ReplyParameters(message_id=message.message_id),
    )
    # Устанавливаем реакцию "обработано"
    try:
        await asyncio.sleep(0.3)
        await message.bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[ReactionTypeEmoji(emoji="👍")],
            is_big=False,
        )
    except Exception as e:
        logger.warning(f"Не удалось установить реакцию 👍: {e}")


def register(router: Router) -> None:
    """Регистрирует обработчики кнопок на переданном Router."""
    router.message.register(button_new_chat, F.text == "🆕 Новый чат")
    router.message.register(button_queue, F.text == "📦 Очередь")
    router.message.register(button_health, F.text == "🏥 Здоровье")
    router.message.register(button_help, F.text == "ℹ️ Помощь")
