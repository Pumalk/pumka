"""
services/importer/ocr.py — распознавание текста на изображениях через easyocr.
"""

import logging
from pathlib import Path
from typing import Optional, List

from PIL import Image

logger = logging.getLogger("pumka.system")

# Глобальный объект OCR (инициализируется при первом вызове)
_ocr_reader = None


def _get_ocr_reader():
    """Ленивая инициализация easyocr."""
    global _ocr_reader
    if _ocr_reader is None:
        import easyocr

        logger.info("Инициализация easyocr (ru, en)...")
        _ocr_reader = easyocr.Reader(["ru", "en"], gpu=False)
        logger.info("easyocr загружен")

    return _ocr_reader


def resize_image_if_needed(image_path: Path, max_size: int = 1500) -> Path:
    """
    Уменьшает размер изображения, если оно больше max_size.

    Args:
        image_path: Путь к исходному изображению
        max_size: Максимальный размер по большей стороне

    Returns:
        Путь к обработанному изображению (может быть тем же)
    """
    try:
        with Image.open(image_path) as img:
            width, height = img.size

            # Если изображение уже маленькое — возвращаем как есть
            if width <= max_size and height <= max_size:
                return image_path

            # Вычисляем новые размеры с сохранением пропорций
            if width > height:
                new_width = max_size
                new_height = int(height * (max_size / width))
            else:
                new_height = max_size
                new_width = int(width * (max_size / height))

            # Ресайз
            resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # Сохраняем в тот же файл (перезаписываем)
            resized.save(image_path, quality=95)

            logger.info(
                f"Изображение уменьшено: {width}x{height} → {new_width}x{new_height}"
            )

            return image_path

    except Exception as e:
        logger.error(f"Ошибка при ресайзе изображения: {e}")
        return image_path  # Возвращаем оригинал при ошибке


def extract_text_from_image(image_path: Path) -> dict:
    """
    Извлекает текст из изображения через OCR.

    Args:
        image_path: Путь к изображению

    Returns:
        Словарь:
        {
            "text": str,
            "confidence": float (средняя уверенность 0-1),
            "error": Optional[str]
        }
    """
    logger.info(f"OCR изображения: {image_path}")

    if not image_path.exists():
        logger.error(f"Изображение не найдено: {image_path}")
        return {"error": f"Файл не найден: {image_path}"}

    try:
        # Уменьшаем изображение если нужно
        processed_path = resize_image_if_needed(image_path)

        # Инициализируем OCR
        reader = _get_ocr_reader()

        # Распознаём текст
        results = reader.readtext(str(processed_path))

        # Собираем текст и уверенность
        text_parts = []
        confidences = []
        for bbox, text, confidence in results:
            text_parts.append(text)
            confidences.append(confidence)

        full_text = " ".join(text_parts).strip()
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        logger.info(
            f"OCR завершён: text_length={len(full_text)}, "
            f"avg_confidence={avg_confidence:.2f}"
        )

        return {
            "text": full_text,
            "confidence": avg_confidence,
            "error": None,
        }

    except Exception as e:
        logger.error(f"Ошибка OCR: {e}")
        return {"error": str(e)}
