"""
interfaces/telegram/handlers/commands.py — обработчики команд.
"""
import asyncio
import logging
from typing import Dict, List
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, ReactionTypeEmoji, ReplyParameters
from aiogram.utils.chat_action import ChatActionSender
from core.config import Config
from core.health_check import run_health_check
from interfaces.telegram.keyboards import main_menu_keyboard
from interfaces.telegram.prompts import HELP_TEXT

logger = logging.getLogger("pumka.system")


async def cmd_start(
    message: Message,
    config: Config,
    chat_memory: Dict[int, List[Dict[str, str]]],
):
    """Обработчик команды /start. Приветствие + очистка памяти диалога."""
    user_id = message.from_user.id
    chat_memory.pop(user_id, None)
    logger.info(f"Команда /start от пользователя {user_id}. Память диалога очищена.")
    await message.answer(
        "Привет! Я Pumka, твой ИИ-ассистент. Чем помочь?",
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


async def cmd_help(message: Message, config: Config):
    """Обработчик команды /help. Краткая справка."""
    logger.info(f"Команда /help от пользователя {message.from_user.id}")
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


async def cmd_queue(message: Message, config: Config):
    """Обработчик команды /queue. Заглушка для Этапа 6."""
    logger.info(f"Команда /queue от пользователя {message.from_user.id}")
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


async def cmd_health(message: Message, config: Config):
    """Обработчик команды /health. Вызов проверки здоровья и форматирование отчёта."""
    logger.info(f"Команда /health от пользователя {message.from_user.id}")
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


def register(router: Router) -> None:
    """Регистрирует обработчики команд на переданном Router."""
    router.message.register(cmd_start, Command("start"))
    router.message.register(cmd_help, Command("help"))
    router.message.register(cmd_queue, Command("queue"))
    router.message.register(cmd_health, Command("health"))
