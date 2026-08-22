"""
interfaces/telegram/prompts.py — системные промпты и текстовые блоки.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict

logger = logging.getLogger("pumka.system")

# Улан-Удэ: UTC+8
ULAN_UDE_UTC_OFFSET_HOURS = 8

LANGUAGE_RULES_BLOCK = (
    "== ПРАВИЛА ЯЗЫКА ==\n"
    "Отвечай только на русском языке.\n"
    "Запрещено использовать китайские иероглифы и любые не-кириллические символы,\n"
    "кроме латинских имён и терминов (Docker, Python, Ollama, Telegram и т.п.).\n"
    "\n"
    "== ВАЖНО ПРО ДАТУ И ВРЕМЯ ==\n"
    "Тебе НЕ НУЖНО вызывать инструменты или системные возможности для получения даты.\n"
    "Текущая дата и время УЖЕ указаны ниже в блоке ТЕКУЩИЙ МОМЕНТ.\n"
    "Используй эту информацию напрямую для ответов на вопросы о дате, времени и дне недели.\n"
    "НИКОГДА не пиши 'у меня нет системных возможностей' — дата уже дана тебе."
)

HELP_TEXT = (
    "📖 Доступные команды и кнопки:\n"
    "/start — начать заново и очистить память диалога\n"
    "/help — эта справка\n"
    "/queue — очередь задач (заглушка)\n"
    "/health — проверка здоровья системы\n"
    "🆕 Новый чат — очистить память диалога\n"
    "Просто напиши текст — я отвечу через агента."
)


def get_current_datetime_ru() -> str:
    """
    Возвращает текущую дату и время на русском языке для Улан-Удэ.
    Формат:
    18 августа 2026 года, 14:25, вторник
    Используется фиксированный UTC+8, без внешних библиотек и locale.
    """
    months = {
        1: "января",
        2: "февраля",
        3: "марта",
        4: "апреля",
        5: "мая",
        6: "июня",
        7: "июля",
        8: "августа",
        9: "сентября",
        10: "октября",
        11: "ноября",
        12: "декабря",
    }
    weekdays = {
        0: "понедельник",
        1: "вторник",
        2: "среда",
        3: "четверг",
        4: "пятница",
        5: "суббота",
        6: "воскресенье",
    }
    tz = timezone(timedelta(hours=ULAN_UDE_UTC_OFFSET_HOURS))
    now = datetime.now(tz)
    return (
        f"{now.day} {months[now.month]} {now.year} года, "
        f"{now.strftime('%H:%M')}, {weekdays[now.weekday()]}"
    )


def get_current_datetime_block() -> str:
    """
    Блок текущего момента для system_prompt.
    """
    return f"== ТЕКУЩИЙ МОМЕНТ ==\nСейчас: {get_current_datetime_ru()}."


def build_system_prompt(
    agent_system_prompt: str,
    history: List[Dict[str, str]],
) -> str:
    """
    Собирает полный system_prompt:
    1. промпт агента;
    2. правила языка;
    3. текущий момент;
    4. история диалога, если она есть.
    """
    from .memory import build_history_block

    parts: List[str] = []
    if agent_system_prompt.strip():
        parts.append(agent_system_prompt.strip())
    parts.append(LANGUAGE_RULES_BLOCK)
    parts.append(get_current_datetime_block())
    history_block = build_history_block(history)
    if history_block:
        parts.append(history_block)
    return "\n".join(parts)
