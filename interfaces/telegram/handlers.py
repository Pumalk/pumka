"""
interfaces/telegram/handlers.py — обработчики команд и сообщений Telegram-бота.
Использует ReplyKeyboard для кнопок под полем ввода.

Подэтап 2.1:
- правила языка;
- текущая дата и время для Улан-Удэ;
- in-memory память диалога на последние 20 сообщений;
- кнопка "Новый чат" вместо "Чат";
- /start очищает память диалога.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Any, Awaitable, Callable, Dict, List

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender
from aiogram.dispatcher.middlewares.base import BaseMiddleware

from core.config import Config
from core.health_check import run_health_check
from core.agent_loader import load_agent
from core.ai_client import get_client
from core.function_calling import process_response
from core.tools import ToolRegistry
from interfaces.telegram.keyboards import main_menu_keyboard

logger = logging.getLogger("pumka.system")
incidents_logger = logging.getLogger("pumka.incidents")

# Лимит итераций function calling
MAX_TOOL_ITERATIONS = 5

# Память диалога: последние 20 сообщений
MEMORY_LIMIT = 20

# Улан-Удэ: UTC+3
ULAN_UDE_UTC_OFFSET_HOURS = 3

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


# ============================================================================
# Память диалога и служебные блоки для system_prompt
# ============================================================================


def get_current_datetime_ru() -> str:
    """
    Возвращает текущую дату и время на русском языке для Улан-Удэ.

    Формат:
        18 августа 2026 года, 14:25, вторник

    Используется фиксированный UTC+3, без внешних библиотек и locale.
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


def add_message_to_memory(
    chat_memory: Dict[int, List[Dict[str, str]]],
    user_id: int,
    role: str,
    text: str,
) -> None:
    """
    Добавляет сообщение в память диалога.

    role: "user" или "bot"
    """
    clean_text = (text or "").strip()
    if not clean_text:
        return

    history = chat_memory.setdefault(user_id, [])
    history.append({"role": role, "text": clean_text})


def trim_memory(
    chat_memory: Dict[int, List[Dict[str, str]]],
    user_id: int,
) -> None:
    """
    Оставляет только последние MEMORY_LIMIT сообщений.
    """
    history = chat_memory.get(user_id)
    if not history:
        return

    if len(history) > MEMORY_LIMIT:
        history[:] = history[-MEMORY_LIMIT:]
        logger.info(
            f"Память обрезана до {len(history)} сообщений для пользователя {user_id}"
        )


def build_history_block(history: List[Dict[str, str]]) -> str:
    """
    Строит блок истории диалога для system_prompt.
    Если история пустая — блок не добавляется.
    """
    if not history:
        return ""

    lines = ["== ИСТОРИЯ ДИАЛОГА (последние сообщения) =="]

    for item in history:
        role = item.get("role", "")
        text = item.get("text", "")

        if role == "user":
            lines.append(f"Пользователь: {text}")
        else:
            lines.append(f"Бот: {text}")

    return "\n".join(lines)


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
    parts: List[str] = []

    if agent_system_prompt.strip():
        parts.append(agent_system_prompt.strip())

    parts.append(LANGUAGE_RULES_BLOCK)
    parts.append(get_current_datetime_block())

    history_block = build_history_block(history)
    if history_block:
        parts.append(history_block)

    return "\n\n".join(parts)


# ============================================================================
# Middleware для проверки доступа
# ============================================================================


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


# ============================================================================
# Обработчики команд
# ============================================================================

router = Router()


@router.message(Command("start"))
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
    )


@router.message(Command("help"))
async def cmd_help(message: Message, config: Config):
    """Обработчик команды /help. Краткая справка."""
    logger.info(f"Команда /help от пользователя {message.from_user.id}")
    await message.answer(HELP_TEXT, reply_markup=main_menu_keyboard())


@router.message(Command("queue"))
async def cmd_queue(message: Message, config: Config):
    """Обработчик команды /queue. Заглушка для Этапа 6."""
    logger.info(f"Команда /queue от пользователя {message.from_user.id}")
    await message.answer(
        "Очередь задач будет доступна на Этапе 6. Пока пусто.",
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("health"))
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
            parse_mode=None,
        )


# ============================================================================
# Обработчики кнопок ReplyKeyboard
# ============================================================================


@router.message(F.text == "🆕 Новый чат")
async def button_new_chat(
    message: Message,
    config: Config,
    chat_memory: Dict[int, List[Dict[str, str]]],
):
    """Кнопка очистки памяти диалога."""
    user_id = message.from_user.id

    chat_memory.pop(user_id, None)

    logger.info(
        f"Кнопка 'Новый чат' от пользователя {user_id}. Память диалога очищена."
    )

    await message.answer(
        "Начат новый чат. Чем помочь?",
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.text == "📦 Очередь")
async def button_queue(message: Message, config: Config):
    logger.info(f"Кнопка 'Очередь' от пользователя {message.from_user.id}")
    await message.answer(
        "Очередь задач будет доступна на Этапе 6. Пока пусто.",
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.text == "🏥 Здоровье")
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
            parse_mode=None,
        )


@router.message(F.text == "ℹ️ Помощь")
async def button_help(message: Message, config: Config):
    logger.info(f"Кнопка 'Помощь' от пользователя {message.from_user.id}")
    await message.answer(HELP_TEXT, reply_markup=main_menu_keyboard())


# ============================================================================
# Обработчик текстовых сообщений (fallback — демо-агент)
# ============================================================================


@router.message(F.text)
async def handle_text_message(
    message: Message,
    config: Config,
    tool_registry: ToolRegistry,
    chat_memory: Dict[int, List[Dict[str, str]]],
):
    """
    Обработчик обычных текстовых сообщений.

    Загружает демо-агента, вызывает LLM, выполняет function calling.
    Использует память диалога, правила языка и текущую дату.
    """
    user_id = message.from_user.id
    user_text = (message.text or "").strip()

    logger.info(f"Текстовое сообщение от пользователя {user_id}: {user_text}")

    if not user_text:
        await message.answer("Напишите ваш вопрос текстом.")
        return

    async with ChatActionSender(
        bot=message.bot, chat_id=message.chat.id, action="typing"
    ):
        agent = load_agent("demo")

        if not agent:
            await message.answer(
                "⚠️ Демо-агент не найден. "
                "Проверьте, что файл agents/builtin/demo.yaml существует."
            )
            return

        try:
            llm_client = get_client(
                config.llm.provider,
                ollama_url=config.llm.ollama_url,
            )
        except ValueError as e:
            await message.answer(f"⚠️ Ошибка конфигурации: {e}")
            return

        tools_for_llm = None
        if agent.tools:
            tools_for_llm = tool_registry.get_openai_tools_format()

        history = chat_memory.get(user_id, [])
        full_system_prompt = build_system_prompt(agent.system_prompt, history)

        logger.info(
            f"Сформирован system_prompt для пользователя {user_id}: "
            f"правила языка=да, дата=да, история={len(history)} сообщений"
        )

        current_prompt = user_text
        iteration = 0
        final_response = ""
        cleaned_response = ""

        while iteration < MAX_TOOL_ITERATIONS:
            iteration += 1
            logger.info(f"Итерация {iteration}: отправка запроса в LLM")

            # Retry при пустом ответе — Ollama иногда отдаёт length=0
            response = ""
            max_retries = 2

            for attempt in range(max_retries):
                try:
                    response = llm_client.generate(
                        prompt=current_prompt,
                        system_prompt=full_system_prompt,
                        tools=tools_for_llm,
                        model=agent.model.name,
                        max_tokens=2048,
                    )
                except Exception as e:
                    logger.error(f"Ошибка при запросе к LLM (попытка {attempt + 1}): {e}")
                    if attempt == max_retries - 1:
                        await message.answer(
                            "⚠️ Не могу связаться с моделью. "
                            "Проверь, что Ollama запущена на хосте."
                        )
                        return
                    await asyncio.sleep(1)
                    continue

                if response and response.strip():
                    break

                logger.warning(
                    f"LLM вернул пустой ответ (попытка {attempt + 1}/{max_retries}). "
                    f"Повтор через 1 секунду..."
                )

                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                    continue

            if not response or not response.strip():
                logger.error("LLM вернул пустой ответ после всех попыток")
                await message.answer("⚠️ Не удалось получить ответ. Попробуйте ещё раз.")
                return

            cleaned_response, tool_results, has_tool_calls = process_response(
                response, agent.model.name, tool_registry
            )

            if not has_tool_calls:
                final_response = cleaned_response
                break

            logger.info(f"Найдено {len(tool_results)} вызовов инструментов")

            tool_results_text = []

            for result in tool_results:
                if result["success"]:
                    tool_results_text.append(
                        f"Результат вызова {result['name']}: {result['result']}"
                    )
                else:
                    tool_results_text.append(
                        f"Ошибка вызова {result['name']}: {result['error']}"
                    )

            current_prompt = (
                f"Предыдущий ответ: {cleaned_response}\n"
                f"Результаты вызовов инструментов:\n"
                + "\n".join(tool_results_text)
                + "\nПродолжай выполнение задачи."
            )
        else:
            logger.warning(
                f"Превышен лимит итераций function calling: {MAX_TOOL_ITERATIONS}"
            )
            final_response = (
                cleaned_response
                + "\n⚠️ Я попытался использовать слишком много инструментов и остановился."
            )

        final_response = (final_response or "").strip()

        # Защита от пустого ответа
        if not final_response:
            logger.warning("LLM вернул пустой ответ")
            await message.answer("⚠️ Не удалось получить ответ. Попробуйте ещё раз.")
            return

        # Сохраняем в память только обычный ответ, не служебные ошибки
        if not final_response.startswith("[ОШИБКА]"):
            add_message_to_memory(chat_memory, user_id, "user", user_text)
            add_message_to_memory(chat_memory, user_id, "bot", final_response)
            trim_memory(chat_memory, user_id)

        if len(final_response) <= 4096:
            await message.answer(final_response, parse_mode=None)
        else:
            chunks = []
            chunk_size = 4096

            for i in range(0, len(final_response), chunk_size):
                chunks.append(final_response[i : i + chunk_size])

            for chunk in chunks:
                await message.answer(chunk, parse_mode=None)


# ============================================================================
# Обработчики не-текстовых сообщений (стикеры, фото, голосовые и т.д.)
# ============================================================================


@router.message(F.sticker)
async def handle_sticker(message: Message, config: Config):
    """Обработчик стикеров."""
    logger.info(f"Стикер от пользователя {message.from_user.id}")
    await message.answer(
        "Пока понимаю только текст 🙂 Голосовые и стикеры появятся на следующих этапах."
    )


@router.message(F.photo)
async def handle_photo(message: Message, config: Config):
    """Обработчик фото."""
    logger.info(f"Фото от пользователя {message.from_user.id}")
    await message.answer(
        "Пока понимаю только текст 🙂 Распознавание изображений появится на следующих этапах."
    )


@router.message(F.voice | F.audio)
async def handle_voice(message: Message, config: Config):
    """Обработчик голосовых и аудио-сообщений."""
    logger.info(f"Голосовое/аудио от пользователя {message.from_user.id}")
    await message.answer(
        "Пока понимаю только текст 🙂 Голосовые сообщения (Whisper) появятся на следующих этапах."
    )


@router.message(F.video)
async def handle_video(message: Message, config: Config):
    """Обработчик видео."""
    logger.info(f"Видео от пользователя {message.from_user.id}")
    await message.answer(
        "Пока понимаю только текст 🙂 Анализ видео появится на следующих этапах."
    )


@router.message(F.document)
async def handle_document(message: Message, config: Config):
    """Обработчик документов."""
    logger.info(f"Документ от пользователя {message.from_user.id}")
    await message.answer(
        "Пока понимаю только текст 🙂 Работа с файлами появится на следующих этапах."
    )


@router.message()
async def handle_unknown(message: Message, config: Config):
    """Обработчик любых остальных типов сообщений."""
    logger.info(f"Неизвестный тип сообщения от пользователя {message.from_user.id}")
    await message.answer("Пока понимаю только текст 🙂 Напишите ваш вопрос.")
