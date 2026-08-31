"""
interfaces/telegram/handlers/import_content.py — обработчики импорта контента.
Этап 6: импорт уходит в RQ-очередь, хэндлер сразу отвечает.
"""
import asyncio
import logging
import re
from pathlib import Path
from typing import Dict, List

from aiogram import Router, F
from aiogram.types import Message, ReactionTypeEmoji, ReplyParameters
from aiogram.filters import Command

from core.config import Config
from services.importer.notes import check_duplicate
from interfaces.telegram.jobs import (
    is_source_in_queue,
    enqueue_import_url,
    enqueue_import_photo,
    enqueue_import_text,
)

logger = logging.getLogger("pumka.system")

# Регулярное выражение для поиска URL в тексте
URL_PATTERN = re.compile(
    r'https?://[^\s<>"{}|\\^`\[\]]+|www\.[^\s<>"{}|\\^`\[\]]+'
)




async def _clear_thinking_reaction(message: Message):
    """
    Снимает реакцию 🤔, установленную ReactionMiddleware.
    Вызывается при мгновенных ответах (дедуп, уже в очереди, слишком коротко).
    """
    try:
        await asyncio.sleep(0.2)
        await message.bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[],  # пустой список = очистить все реакции
            is_big=False,
        )
    except Exception as e:
        logger.warning(f"Не удалось снять реакцию: {e}")

def extract_urls(text: str) -> List[str]:
    """Извлекает все URL из текста."""
    urls = URL_PATTERN.findall(text)
    normalized = []
    for url in urls:
        if url.startswith("www."):
            url = "https://" + url
        normalized.append(url)
    return normalized


async def handle_url_message(
    message: Message,
    config: Config,
    chat_memory: Dict[int, List[Dict[str, str]]],
):
    """Обработчик сообщений с URL — ставит задачу в очередь."""
    user_id = message.from_user.id
    text = message.text or ""
    urls = extract_urls(text)

    if not urls:
        return

    url = urls[0]
    logger.info(f"Обнаружен URL в сообщении от {user_id}: {url}")


    # Проверка дедупа в imported.json
    data_dir = Path(config.paths.project) / "data"
    duplicate = check_duplicate(data_dir, url=url)
    if duplicate:
        await _clear_thinking_reaction(message)
        await message.answer(
            f"ℹ️ Уже обработано {duplicate.get('date', '')}: {duplicate.get('note_path', '')}",
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
        return

    # Проверка, что такой же URL не стоит в очереди
    if is_source_in_queue(url):
        await _clear_thinking_reaction(message)
        await message.answer(
            "ℹ️ Уже обрабатывается",
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
        return

    # Реакция 🤔 уже установлена ReactionMiddleware — просто ставим в очередь
    job_id = enqueue_import_url(url, message.chat.id, message.message_id)
    logger.info(f"Задача импорта URL поставлена в очередь: {job_id}")

    await message.answer(
        f"⏳ Задача в очереди: {url[:80]}",
        reply_parameters=ReplyParameters(message_id=message.message_id),
    )


async def handle_photo_import(
    message: Message,
    config: Config,
    chat_memory: Dict[int, List[Dict[str, str]]],
):
    """Обработчик фото — скачивает и ставит задачу в очередь."""
    user_id = message.from_user.id
    logger.info(f"Фото от пользователя {user_id} — ставлю в очередь")


    # Скачиваем фото
    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    temp_dir = Path(config.paths.project) / "data" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{photo.file_id}.jpg"
    await message.bot.download_file(file.file_path, temp_path)

    # Проверка дедупа в imported.json (по содержимому файла)
    data_dir = Path(config.paths.project) / "data"
    try:
        with open(temp_path, "rb") as f:
            content_hash = f.read()
        duplicate = check_duplicate(data_dir, content=content_hash.decode("latin-1"))
        if duplicate:
            await _clear_thinking_reaction(message)
            await message.answer(
                f"ℹ️ Уже обработано {duplicate.get('date', '')}",
                reply_parameters=ReplyParameters(message_id=message.message_id),
            )
            temp_path.unlink()
            return
    except Exception as e:
        logger.warning(f"Не удалось проверить дедуп фото: {e}")

    # Проверка, что такое же фото не стоит в очереди
    source = f"фото:{photo.file_id}"
    if is_source_in_queue(source):
        await _clear_thinking_reaction(message)
        await message.answer(
            "ℹ️ Уже обрабатывается",
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
        temp_path.unlink()
        return

    # Реакция 🤔 уже установлена ReactionMiddleware — просто ставим в очередь
    caption = message.caption
    job_id = enqueue_import_photo(
        str(temp_path), caption, message.chat.id, message.message_id, source
    )
    logger.info(f"Задача импорта фото поставлена в очередь: {job_id}")

    await message.answer(
        "⏳ Задача в очереди: фото",
        reply_parameters=ReplyParameters(message_id=message.message_id),
    )


async def cmd_save(
    message: Message,
    config: Config,
    chat_memory: Dict[int, List[Dict[str, str]]],
):
    """Обработчик команды /save — ставит задачу импорта текста в очередь."""
    user_id = message.from_user.id
    logger.info(f"Команда /save от пользователя {user_id}")

    # Определяем, что сохранять
    text_to_save = None
    if message.reply_to_message:
        replied = message.reply_to_message
        if replied.text:
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
        if message.text and len(message.text) > 6:
            text_to_save = message.text[6:].strip()
        else:
            await message.answer(
                "Использование: /save <текст> или reply сообщением с /save",
                reply_parameters=ReplyParameters(message_id=message.message_id),
            )
            return

    if not text_to_save or len(text_to_save) < 20:
        await _clear_thinking_reaction(message)
        await message.answer(
            "Слишком коротко для заметки (мин. 20 символов)",
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
        return


    # Проверка дедупа в imported.json
    data_dir = Path(config.paths.project) / "data"
    duplicate = check_duplicate(data_dir, content=text_to_save)
    if duplicate:
        await _clear_thinking_reaction(message)
        await message.answer(
            f"ℹ️ Уже обработано {duplicate.get('date', '')}",
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
        return

    # Проверка, что такой же текст не стоит в очереди
    source = f"текст:{text_to_save[:100]}"
    if is_source_in_queue(source):
        await _clear_thinking_reaction(message)
        await message.answer(
            "ℹ️ Уже обрабатывается",
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
        return

    # Реакция 🤔 уже установлена ReactionMiddleware — просто ставим в очередь
    job_id = enqueue_import_text(text_to_save, message.chat.id, message.message_id)
    logger.info(f"Задача импорта текста поставлена в очередь: {job_id}")

    await message.answer(
        f"⏳ Задача в очереди: текст ({len(text_to_save)} символов)",
        reply_parameters=ReplyParameters(message_id=message.message_id),
    )


def register(router: Router) -> None:
    """Регистрирует обработчики импорта контента."""
    router.message.register(cmd_save, Command("save"))
    router.message.register(handle_photo_import, F.photo)
    router.message.register(handle_url_message, F.text.regexp(URL_PATTERN))
