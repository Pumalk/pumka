"""
interfaces/telegram/handlers/text.py — обработчик текстовых сообщений с очередью.
"""
import asyncio
import logging
from typing import Dict, List
from aiogram import Router, F
from aiogram.types import Message, ReactionTypeEmoji, ReplyParameters
from aiogram.utils.chat_action import ChatActionSender
from core.config import Config
from core.agent_loader import load_agent
from core.ai_client import get_client
from core.function_calling import process_response
from core.tools import ToolRegistry
from interfaces.telegram.queue_users import user_queues, ensure_worker
from interfaces.telegram.memory import add_message_to_memory, trim_memory
from interfaces.telegram.prompts import build_system_prompt

logger = logging.getLogger("pumka.system")

# Лимит итераций function calling
MAX_TOOL_ITERATIONS = 5


async def process_message_worker(
    message: Message,
    is_old_message: bool,
    message_age_seconds: float,
    config: Config,
    tool_registry: ToolRegistry,
    chat_memory: Dict[int, List[Dict[str, str]]],
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


async def _process_with_reactions(
    message: Message,
    is_old: bool,
    age_seconds: float,
    config: Config,
    tool_registry: ToolRegistry,
    chat_memory: Dict[int, List[Dict[str, str]]],
):
    """
    Обёртка вокруг process_message_worker, которая добавляет реакции.
    🤔 — перед обработкой, 👍 — после успешной обработки.
    """
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
            message, is_old, age_seconds, config, tool_registry, chat_memory
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
    ensure_worker(user_id, _process_with_reactions, config, tool_registry, chat_memory)


def register(router: Router) -> None:
    """Регистрирует обработчик текстовых сообщений на переданном Router."""
    router.message.register(handle_text_message, F.text)
