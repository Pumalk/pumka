"""
interfaces/telegram/handlers/import_content.py — обработчики импорта контента.
"""
import asyncio
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from aiogram import Router, F
from aiogram.types import Message, ReactionTypeEmoji, ReplyParameters, CallbackQuery
from aiogram.utils.chat_action import ChatActionSender
from aiogram.filters import Command

from core.config import Config
from core.ai_client import get_client
from services.importer.pipeline import import_url, import_photo, import_text

logger = logging.getLogger("pumka.system")

# Регулярное выражение для поиска URL в тексте
URL_PATTERN = re.compile(
    r'https?://[^\s<>"{}|\\^`\[\]]+|www\.[^\s<>"{}|\\^`\[\]]+'
)


def extract_urls(text: str) -> List[str]:
    """Извлекает все URL из текста."""
    urls = URL_PATTERN.findall(text)
    # Нормализуем www. ссылки
    normalized = []
    for url in urls:
        if url.startswith("www."):
            url = "https://" + url
        normalized.append(url)
    return normalized


async def progress_callback_factory(message: Message):
    """Создаёт функцию для отправки прогресс-уведомлений."""
    async def send_progress(text: str):
        try:
            await message.answer(text)
        except Exception as e:
            logger.warning(f"Не удалось отправить прогресс: {e}")
    return send_progress


async def handle_url_message(
    message: Message,
    config: Config,
    chat_memory: Dict[int, List[Dict[str, str]]],
):
    """Обработчик сообщений с URL — автоимпорт."""
    user_id = message.from_user.id
    text = message.text or ""
    urls = extract_urls(text)
    
    if not urls:
        return  # Нет URL — пропускаем
    
    logger.info(f"Обнаружен URL в сообщении от {user_id}: {urls[0]}")
    
    # Реакция "думаю"
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
    
    url = urls[0]  # Берём первую ссылку
    
    # Создаём LLM-клиент
    try:
        llm_client = get_client(
            config.llm.provider,
            ollama_url=config.llm.ollama_url,
        )
    except ValueError as e:
        await message.answer(f"⚠️ Ошибка конфигурации: {e}")
        return
    
    # Определяем модель для саммари
    profile = config.llm.profiles.get(config.llm.default_profile)
    model_name = getattr(profile, "light", "Qwen2.5-3B-Instruct-AWQ") if profile else "Qwen2.5-3B-Instruct-AWQ"
    
    # Создаём callback для прогресса
    progress_cb = await progress_callback_factory(message)
    
    # Импортируем
    result = await import_url(
        url=url,
        vault_path=Path(config.paths.vault_path),
        data_dir=Path(config.paths.project) / "data",
        llm_client=llm_client,
        model_name=model_name,
        progress_callback=progress_cb,
    )
    
    # Отправляем результат
    if result["success"]:
        card = f"""✅ *Импортировано*

*{result['title']}*
Тематика: {result.get('topic', 'Другие тематики')}
Теги: {', '.join(result.get('tags', []))}

{result['summary'][:500]}...

📁 Заметка: `{result['note_path']}`"""
        
        await message.answer(
            card,
            parse_mode="Markdown",
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
    elif result["error"] == "duplicate":
        await message.answer(
            f"ℹ️ {result['summary']}",
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
    else:
        await message.answer(
            f"⚠️ Не удалось обработать {url}. Подробности в «Проблемные материалы»",
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
    
    # Реакция "готово"
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


async def handle_photo_import(
    message: Message,
    config: Config,
    chat_memory: Dict[int, List[Dict[str, str]]],
):
    """Обработчик фото — импорт через OCR."""
    user_id = message.from_user.id
    logger.info(f"Фото от пользователя {user_id} — импорт через OCR")
    
    # Реакция "думаю"
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
    
    # Скачиваем фото
    photo = message.photo[-1]  # Берём самое большое разрешение
    file = await message.bot.get_file(photo.file_id)
    
    # Сохраняем во временную папку
    temp_dir = Path(config.paths.project) / "data" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{photo.file_id}.jpg"
    
    await message.bot.download_file(file.file_path, temp_path)
    
    # Создаём LLM-клиент
    try:
        llm_client = get_client(
            config.llm.provider,
            ollama_url=config.llm.ollama_url,
        )
    except ValueError as e:
        await message.answer(f"⚠️ Ошибка конфигурации: {e}")
        return
    
    profile = config.llm.profiles.get(config.llm.default_profile)
    model_name = getattr(profile, "light", "Qwen2.5-3B-Instruct-AWQ") if profile else "Qwen2.5-3B-Instruct-AWQ"
    
    # Caption (подпись к фото)
    caption = message.caption
    
    # Создаём callback для прогресса
    progress_cb = await progress_callback_factory(message)
    
    # Импортируем
    result = await import_photo(
        image_path=temp_path,
        caption=caption,
        vault_path=Path(config.paths.vault_path),
        data_dir=Path(config.paths.project) / "data",
        llm_client=llm_client,
        model_name=model_name,
        progress_callback=progress_cb,
    )
    
    # Отправляем результат
    if result["success"]:
        card = f"""✅ *Изображение импортировано*

*{result['title']}*
Теги: {', '.join(result.get('tags', []))}

{result['summary'][:500]}...

📁 Заметка: `{result['note_path']}`"""
        
        await message.answer(
            card,
            parse_mode="Markdown",
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
    elif result["error"] == "duplicate":
        await message.answer(
            f"ℹ️ {result['summary']}",
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
    else:
        await message.answer(
            f"⚠️ Не удалось обработать изображение. Подробности в «Проблемные материалы»",
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
    
    # Реакция "готово"
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
    
    # Удаляем временный файл
    try:
        if temp_path.exists():
            temp_path.unlink()
    except Exception as e:
        logger.warning(f"Не удалось удалить временный файл: {e}")


async def cmd_save(
    message: Message,
    config: Config,
    chat_memory: Dict[int, List[Dict[str, str]]],
):
    """Обработчик команды /save — сохранение текста."""
    user_id = message.from_user.id
    logger.info(f"Команда /save от пользователя {user_id}")
    
    # Проверяем, это reply на другое сообщение или текст после /save
    text_to_save = None
    
    if message.reply_to_message:
        # Reply на другое сообщение
        replied = message.reply_to_message
        if replied.text:
            # Проверяем, нет ли URL в replied сообщении
            urls = extract_urls(replied.text)
            if urls:
                # Если есть URL — запускаем импорт ссылки
                await handle_url_message(replied, config, chat_memory)
                return
            else:
                text_to_save = replied.text
        elif replied.caption:
            text_to_save = replied.caption
        else:
            await message.answer(
                "/save сохраняет только текст или подпись",
                reply_parameters=ReplyParameters(message_id=message.message_id),
            )
            return
    else:
        # Текст после /save
        if message.text and len(message.text) > 6:  # "/save " + текст
            text_to_save = message.text[6:].strip()
        else:
            await message.answer(
                "Использование: /save <текст> или reply сообщением с /save",
                reply_parameters=ReplyParameters(message_id=message.message_id),
            )
            return
    
    if not text_to_save or len(text_to_save) < 20:
        await message.answer(
            "Слишком коротко для заметки (мин. 20 символов)",
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
        return
    
    # Реакция "думаю"
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
    
    # Создаём LLM-клиент
    try:
        llm_client = get_client(
            config.llm.provider,
            ollama_url=config.llm.ollama_url,
        )
    except ValueError as e:
        await message.answer(f"⚠️ Ошибка конфигурации: {e}")
        return
    
    profile = config.llm.profiles.get(config.llm.default_profile)
    model_name = getattr(profile, "light", "Qwen2.5-3B-Instruct-AWQ") if profile else "Qwen2.5-3B-Instruct-AWQ"
    
    # Создаём callback для прогресса
    progress_cb = await progress_callback_factory(message)
    
    # Импортируем текст
    result = await import_text(
        text=text_to_save,
        vault_path=Path(config.paths.vault_path),
        data_dir=Path(config.paths.project) / "data",
        llm_client=llm_client,
        model_name=model_name,
        progress_callback=progress_cb,
    )
    
    # Отправляем результат
    if result["success"]:
        card = f"""✅ *Текст сохранён*

*{result['title']}*
Тематика: {result.get('topic', 'Другие тематики')}
Теги: {', '.join(result.get('tags', []))}

{result['summary'][:500]}...

📁 Заметка: `{result['note_path']}`"""
        
        await message.answer(
            card,
            parse_mode="Markdown",
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
    elif result["error"] == "duplicate":
        await message.answer(
            f"ℹ️ {result['summary']}",
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
    elif result["error"] == "too_short":
        await message.answer(
            "Слишком коротко для заметки (мин. 20 символов)",
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
    else:
        await message.answer(
            f"⚠️ Не удалось сохранить текст. Подробности в «Проблемные материалы»",
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
    
    # Реакция "готово"
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
    """Регистрирует обработчики импорта контента."""
    # Команда /save
    router.message.register(cmd_save, Command("save"))
    
    # Фото (до текстовых обработчиков!)
    router.message.register(handle_photo_import, F.photo)
    
    # URL в тексте (до обычных текстовых обработчиков!)
    router.message.register(handle_url_message, F.text.regexp(URL_PATTERN))
