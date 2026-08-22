"""
interfaces/telegram/handlers/media.py — заглушки не-текстовых сообщений.
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, ReplyParameters
from core.config import Config

logger = logging.getLogger("pumka.system")


async def handle_sticker(message: Message, config: Config):
    """Обработчик стикеров."""
    logger.info(f"Стикер от пользователя {message.from_user.id}")
    await message.answer(
        "Пока понимаю только текст 🙂 Голосовые и стикеры появятся на следующих этапах.",
        reply_parameters=ReplyParameters(message_id=message.message_id),
    )


async def handle_photo(message: Message, config: Config):
    """Обработчик фото."""
    logger.info(f"Фото от пользователя {message.from_user.id}")
    await message.answer(
        "Пока понимаю только текст 🙂 Распознавание изображений появится на следующих этапах.",
        reply_parameters=ReplyParameters(message_id=message.message_id),
    )


async def handle_voice(message: Message, config: Config):
    """Обработчик голосовых и аудио-сообщений."""
    logger.info(f"Голосовое/аудио от пользователя {message.from_user.id}")
    await message.answer(
        "Пока понимаю только текст 🙂 Голосовые сообщения (Whisper) появятся на следующих этапах.",
        reply_parameters=ReplyParameters(message_id=message.message_id),
    )


async def handle_video(message: Message, config: Config):
    """Обработчик видео."""
    logger.info(f"Видео от пользователя {message.from_user.id}")
    await message.answer(
        "Пока понимаю только текст 🙂 Анализ видео появится на следующих этапах.",
        reply_parameters=ReplyParameters(message_id=message.message_id),
    )


async def handle_document(message: Message, config: Config):
    """Обработчик документов."""
    logger.info(f"Документ от пользователя {message.from_user.id}")
    await message.answer(
        "Пока понимаю только текст 🙂 Работа с файлами появится на следующих этапах.",
        reply_parameters=ReplyParameters(message_id=message.message_id),
    )


async def handle_unknown(message: Message, config: Config):
    """Обработчик любых остальных типов сообщений."""
    logger.info(f"Неизвестный тип сообщения от пользователя {message.from_user.id}")
    await message.answer(
        "Пока понимаю только текст 🙂 Напишите ваш вопрос.",
        reply_parameters=ReplyParameters(message_id=message.message_id),
    )


def register(router: Router) -> None:
    """Регистрирует обработчики медиа на переданном Router."""
    router.message.register(handle_sticker, F.sticker)
    router.message.register(handle_photo, F.photo)
    router.message.register(handle_voice, F.voice | F.audio)
    router.message.register(handle_video, F.video)
    router.message.register(handle_document, F.document)
    router.message.register(handle_unknown)
