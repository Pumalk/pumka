"""
interfaces/telegram/handlers.py — обработчики команд и сообщений Telegram-бота.
Подэтап 2.2:
- реакции на сообщения (🤔 думаю, 👏 в очереди, 😱 получил с опозданием, 👍 обработано);
- очередь сообщений per-user;
- обработка "старых" сообщений (офлайн);
- текущая дата и время для Улан-Удэ (UTC+8);
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
from aiogram.types import Message, ReactionTypeEmoji, ReplyParameters
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
# Улан-Удэ: UTC+8
ULAN_UDE_UTC_OFFSET_HOURS = 8
# Порог "старого" сообщения (секунды)
OLD_MESSAGE_THRESHOLD = 300

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
# Очередь сообщений per-user
# ============================================================================
user_queues: Dict[int, asyncio.Queue] = {}
user_workers: Dict[int, asyncio.Task] = {}


# ============================================================================
# Middleware для реакций и обработки старых сообщений
# ============================================================================
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


# ============================================================================
# Память диалога и служебные блоки для system_prompt
# ============================================================================
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
    return "\n".join(parts)


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
# Worker для обработки очереди сообщений
# ============================================================================
async def process_message_worker(
    message: Message,
    config: Config,
    tool_registry: ToolRegistry,
    chat_memory: Dict[int, List[Dict[str, str]]],
    is_old_message: bool,
    message_age_seconds: float,
):
    """
    Воркер для обработки одного сообщения из очереди.
    """
    user_id = message.from_user.id
    user_text = (message.text or "").strip()

    if not user_text:
        await message.answer(
            "Напишите ваш вопрос текстом.",
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
        return

    # Обработка старых сообщений
    if is_old_message:
        minutes_offline = int(message_age_seconds / 60)
        prefix = f"😱 Извини, я был недоступен около {minutes_offline} мин. Отвечаю на ваше сообщение.\n\n"
    else:
        prefix = ""

    logger.info(f"Обработка сообщения от пользователя {user_id}: {user_text}")

    async with ChatActionSender(
        bot=message.bot, chat_id=message.chat.id, action="typing"
    ):
        agent = load_agent("demo")
        if not agent:
            await message.answer(
                "⚠️ Демо-агент не найден. "
                "Проверьте, что файл agents/builtin/demo.yaml существует.",
                reply_parameters=ReplyParameters(message_id=message.message_id),
            )
            return

        try:
            llm_client = get_client(
                config.llm.provider,
                ollama_url=config.llm.ollama_url,
            )
        except ValueError as e:
            await message.answer(
                f"⚠️ Ошибка конфигурации: {e}",
                reply_parameters=ReplyParameters(message_id=message.message_id),
            )
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
                    logger.error(
                        f"Ошибка при запросе к LLM (попытка {attempt + 1}): {e}"
                    )
                    if attempt == max_retries - 1:
                        await message.answer(
                            "⚠️ Не могу связаться с моделью. "
                            "Проверь, что Ollama запущена на хосте.",
                            reply_parameters=ReplyParameters(
                                message_id=message.message_id
                            ),
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
                await message.answer(
                    "⚠️ Не удалось получить ответ. Попробуйте ещё раз.",
                    reply_parameters=ReplyParameters(message_id=message.message_id),
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

        if not final_response:
            logger.warning("LLM вернул пустой ответ")
            await message.answer(
                "⚠️ Не удалось получить ответ. Попробуйте ещё раз.",
                reply_parameters=ReplyParameters(message_id=message.message_id),
            )
            return

        # Сохраняем в память только обычный ответ, не служебные ошибки
        if not final_response.startswith("[ОШИБКА]"):
            add_message_to_memory(chat_memory, user_id, "user", user_text)
            add_message_to_memory(chat_memory, user_id, "bot", final_response)
            trim_memory(chat_memory, user_id)

        # Добавляем префикс для старых сообщений
        if prefix:
            final_response = prefix + final_response

        # Отправляем ответ как reply
        if len(final_response) <= 4096:
            await message.answer(
                final_response,
                reply_parameters=ReplyParameters(message_id=message.message_id),
                parse_mode=None,
            )
        else:
            chunks = []
            chunk_size = 4096
            for i in range(0, len(final_response), chunk_size):
                chunks.append(final_response[i : i + chunk_size])
            for chunk in chunks:
                await message.answer(
                    chunk,
                    reply_parameters=ReplyParameters(message_id=message.message_id),
                    parse_mode=None,
                )


async def user_queue_worker(
    user_id: int,
    config: Config,
    tool_registry: ToolRegistry,
    chat_memory: Dict[int, List[Dict[str, str]]],
):
    """
    Воркер для обработки очереди сообщений одного пользователя.
    """
    queue = user_queues.get(user_id)
    if not queue:
        return

    while not queue.empty():
        try:
            message_data = await asyncio.wait_for(queue.get(), timeout=1.0)
            message = message_data["message"]
            is_old = message_data["is_old"]
            age_seconds = message_data["age_seconds"]

            logger.info(f"Воркер обрабатывает сообщение от пользователя {user_id}")

            # Устанавливаем реакцию "думаю"
            try:
                await asyncio.sleep(0.3)
                await message.bot.set_message_reaction(
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reaction=[ReactionTypeEmoji(emoji="🤔")],
                    is_big=False,
                )
            except Exception as e:
                logger.warning(f"Не удалось установить реакцию 🤔: {e}")

            try:
                await process_message_worker(
                    message, config, tool_registry, chat_memory, is_old, age_seconds
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
            except Exception as e:
                logger.error(f"Ошибка при обработке сообщения: {e}")
                try:
                    await message.answer(
                        f"⚠️ Произошла ошибка: {e}",
                        reply_parameters=ReplyParameters(message_id=message.message_id),
                    )
                except:
                    pass
            finally:
                queue.task_done()
        except asyncio.TimeoutError:
            break
        except Exception as e:
            logger.error(f"Ошибка в воркере очереди: {e}")
            break

    # Удаляем воркер если очередь пуста
    if queue.empty() and user_id in user_workers:
        del user_workers[user_id]
        logger.info(f"Воркер для пользователя {user_id} завершён")


# ============================================================================
# Обработчики команд
# ============================================================================
router = Router()

router.message.outer_middleware(ReactionMiddleware())


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


@router.message(Command("help"))
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


@router.message(Command("queue"))
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
    # Новый чат = забыть всё, включая неотвеченные вопросы в очереди
    queue = user_queues.get(user_id)
    if queue is not None:
        cleared = 0
        while not queue.empty():
            try:
                queue.get_nowait()
                queue.task_done()
                cleared += 1
            except asyncio.QueueEmpty:
                break
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


@router.message(F.text == "📦 Очередь")
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


@router.message(F.text == "ℹ️ Помощь")
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


# ============================================================================
# Обработчик текстовых сообщений (с очередью)
# ============================================================================
@router.message(F.text)
async def handle_text_message(
    message: Message,
    config: Config,
    tool_registry: ToolRegistry,
    chat_memory: Dict[int, List[Dict[str, str]]],
    is_old_message: bool = False,
    message_age_seconds: float = 0,
):
    """
    Обработчик обычных текстовых сообщений с поддержкой очереди.
    """
    user_id = message.from_user.id

    # Создаём очередь для пользователя если её нет
    if user_id not in user_queues:
        user_queues[user_id] = asyncio.Queue()
        logger.info(f"Создана очередь для пользователя {user_id}")

    queue = user_queues[user_id]

    # Если очередь не пуста — добавляем реакцию "в очереди"
    if not queue.empty():
        try:
            await asyncio.sleep(0.3)
            await message.bot.set_message_reaction(
                chat_id=message.chat.id,
                message_id=message.message_id,
                reaction=[ReactionTypeEmoji(emoji="👏")],
                is_big=False,
            )
        except Exception as e:
            logger.warning(f"Не удалось установить реакцию 👏: {e}")

    # Добавляем сообщение в очередь
    await queue.put(
        {
            "message": message,
            "is_old": is_old_message,
            "age_seconds": message_age_seconds,
        }
    )

    # Запускаем воркер если его нет
    if user_id not in user_workers or user_workers[user_id].done():
        task = asyncio.create_task(
            user_queue_worker(user_id, config, tool_registry, chat_memory)
        )
        user_workers[user_id] = task
        logger.info(f"Запущен воркер для пользователя {user_id}")


# ============================================================================
# Обработчики не-текстовых сообщений (стикеры, фото, голосовые и т.д.)
# ============================================================================
@router.message(F.sticker)
async def handle_sticker(message: Message, config: Config):
    """Обработчик стикеров."""
    logger.info(f"Стикер от пользователя {message.from_user.id}")
    await message.answer(
        "Пока понимаю только текст 🙂 Голосовые и стикеры появятся на следующих этапах.",
        reply_parameters=ReplyParameters(message_id=message.message_id),
    )


@router.message(F.photo)
async def handle_photo(message: Message, config: Config):
    """Обработчик фото."""
    logger.info(f"Фото от пользователя {message.from_user.id}")
    await message.answer(
        "Пока понимаю только текст 🙂 Распознавание изображений появится на следующих этапах.",
        reply_parameters=ReplyParameters(message_id=message.message_id),
    )


@router.message(F.voice | F.audio)
async def handle_voice(message: Message, config: Config):
    """Обработчик голосовых и аудио-сообщений."""
    logger.info(f"Голосовое/аудио от пользователя {message.from_user.id}")
    await message.answer(
        "Пока понимаю только текст 🙂 Голосовые сообщения (Whisper) появятся на следующих этапах.",
        reply_parameters=ReplyParameters(message_id=message.message_id),
    )


@router.message(F.video)
async def handle_video(message: Message, config: Config):
    """Обработчик видео."""
    logger.info(f"Видео от пользователя {message.from_user.id}")
    await message.answer(
        "Пока понимаю только текст 🙂 Анализ видео появится на следующих этапах.",
        reply_parameters=ReplyParameters(message_id=message.message_id),
    )


@router.message(F.document)
async def handle_document(message: Message, config: Config):
    """Обработчик документов."""
    logger.info(f"Документ от пользователя {message.from_user.id}")
    await message.answer(
        "Пока понимаю только текст 🙂 Работа с файлами появится на следующих этапах.",
        reply_parameters=ReplyParameters(message_id=message.message_id),
    )


@router.message()
async def handle_unknown(message: Message, config: Config):
    """Обработчик любых остальных типов сообщений."""
    logger.info(f"Неизвестный тип сообщения от пользователя {message.from_user.id}")
    await message.answer(
        "Пока понимаю только текст 🙂 Напишите ваш вопрос.",
        reply_parameters=ReplyParameters(message_id=message.message_id),
    )
