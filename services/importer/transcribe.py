"""
services/importer/transcribe.py — транскрибация аудио через faster-whisper.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("pumka.system")

# Глобальный объект модели (инициализируется при первом вызове)
_whisper_model = None


def _get_whisper_model():
    """Ленивая инициализация модели faster-whisper."""
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel

        logger.info("Инициализация faster-whisper модели (small, int8, CPU)...")
        _whisper_model = WhisperModel(
            "small",
            device="cpu",
            compute_type="int8",
        )
        logger.info("faster-whisper модель загружена")

    return _whisper_model


def transcribe_audio(audio_path: Path) -> dict:
    """
    Транскрибирует аудиофайл в текст.

    Args:
        audio_path: Путь к аудиофайлу

    Returns:
        Словарь:
        {
            "text": str,
            "language": str,
            "duration": float (секунды),
            "error": Optional[str]
        }
    """
    logger.info(f"Транскрибация аудио: {audio_path}")

    if not audio_path.exists():
        logger.error(f"Аудиофайл не найден: {audio_path}")
        return {"error": f"Файл не найден: {audio_path}"}

    try:
        model = _get_whisper_model()

        # Транскрибация
        segments, info = model.transcribe(
            str(audio_path),
            beam_size=5,
            language=None,  # Автоопределение языка
            vad_filter=True,  # Фильтр голосовой активности
        )

        # Собираем текст из сегментов
        text_parts = []
        for segment in segments:
            text_parts.append(segment.text)

        full_text = " ".join(text_parts).strip()

        logger.info(
            f"Транскрибация завершена: language={info.language}, "
            f"duration={info.duration:.1f}s, text_length={len(full_text)}"
        )

        return {
            "text": full_text,
            "language": info.language,
            "duration": info.duration,
            "error": None,
        }

    except Exception as e:
        logger.error(f"Ошибка транскрибации: {e}")
        return {"error": str(e)}
