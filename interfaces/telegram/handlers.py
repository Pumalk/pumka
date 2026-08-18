"""
interfaces/telegram/handlers.py — обработчики команд и сообщений Telegram-бота.
Использует ReplyKeyboard для кнопок под полем ввода.
"""

import asyncio
import logging
from typing import Optional, Any, Awaitable, Callable, Dict
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
async def cmd_start(message: Message, config: Config):
    """Обработчик команды /start. Приветствие + показ клавиатуры."""
    logger.info(f"Команда /start от пользователя {message.from_user.id}")
    await message.answer(
        "Привет! Я Pumka, твой ИИ-ассистент. Чем помочь?",
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message, config: Config):
    """Обработчик команды /help. Краткая справка."""
    logger.info(f"Команда /help от пользователя {message.from_user.id}")
    help_text = (
        "📖 Доступные команды:\n\n"
        "/start — Главное меню\n"
        "/help — Эта справка\n"
        "/queue — Очередь задач (заглушка)\n"
        "/health — Проверка здоровья системы"
    )
    await message.answer(help_text, reply_markup=main_menu_keyboard())


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
        report_text, reply_markup=main_menu_keyboard(), parse_mode=None
    )


# ============================================================================
# Обработчики кнопок ReplyKeyboard
# ============================================================================


@router.message(F.text == "💬 Чат")
async def button_chat(message: Message, config: Config):
    logger.info(f"Кнопка 'Чат' от пользователя {message.from_user.id}")
    await message.answer("Напишите ваш вопрос.", reply_markup=main_menu_keyboard())


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
        report_text, reply_markup=main_menu_keyboard(), parse_mode=None
    )


@router.message(F.text == "ℹ️ Помощь")
async def button_help(message: Message, config: Config):
    logger.info(f"Кнопка 'Помощь' от пользователя {message.from_user.id}")
    help_text = (
        "📖 Доступные команды:\n\n"
        "/start — Главное меню\n"
        "/help — Эта справка\n"
        "/queue — Очередь задач (заглушка)\n"
        "/health — Проверка здоровья системы"
    )
    await message.answer(help_text, reply_markup=main_menu_keyboard())


# ============================================================================
# Обработчик текстовых сообщений (fallback — демо-агент)
# ============================================================================


@router.message(F.text)
async def handle_text_message(
    message: Message, config: Config, tool_registry: ToolRegistry
):
    """
    Обработчик обычных текстовых сообщений.
    Загружает демо-агента, вызывает LLM, выполняет function calling.
    """
    logger.info(
        f"Текстовое сообщение от пользователя {message.from_user.id}: {message.text}"
    )

    async with ChatActionSender(
        bot=message.bot, chat_id=message.chat.id, action="typing"
    ):
        agent = load_agent("demo")

        if not agent:
            await message.answer(
                "⚠️ Демо-агент не найден. Проверьте, что файл agents/builtin/demo.yaml существует."
            )
            return

        try:
            llm_client = get_client(
                config.llm.provider, ollama_url=config.llm.ollama_url
            )
        except ValueError as e:
            await message.answer(f"⚠️ Ошибка конфигурации: {e}")
            return

        tools_for_llm = None
        if agent.tools:
            tools_for_llm = tool_registry.get_openai_tools_format()

        current_prompt = message.text
        iteration = 0
        final_response = ""
        cleaned_response = ""

        while iteration < MAX_TOOL_ITERATIONS:
            iteration += 1
            logger.info(f"Итерация {iteration}: отправка запроса в LLM")

            try:
                response = llm_client.generate(
                    prompt=current_prompt,
                    system_prompt=agent.system_prompt,
                    tools=tools_for_llm,
                    model=agent.model.name,
                    max_tokens=2048,
                )
            except Exception as e:
                logger.error(f"Ошибка при запросе к LLM: {e}")
                await message.answer(
                    "⚠️ Не могу связаться с моделью. Проверь, что Ollama запущена на хосте."
                )
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
                + "\n\nПродолжай выполнение задачи."
            )

        else:
            logger.warning(
                f"Превышен лимит итераций function calling: {MAX_TOOL_ITERATIONS}"
            )
            final_response = (
                cleaned_response
                + "\n\n⚠️ Я попытался использовать слишком много инструментов и остановился."
            )

        # Защита от пустого ответа
        if not final_response or not final_response.strip():
            logger.warning("LLM вернул пустой ответ")
            await message.answer("⚠️ Не удалось получить ответ. Попробуйте ещё раз.")
            return

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
        "Пока понимаю только текст 🙂 Голосовые сообщения (Whisper) появятся на подэтапе 2.1."
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
