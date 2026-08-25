"""
services/importer/summarizer.py — саммари и теги через LLM.
"""

import logging
import re
from typing import List, Dict, Any, Optional

logger = logging.getLogger("pumka.system")

# Размер куска для чанкования (символы)
CHUNK_SIZE = 12000


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> List[str]:
    """
    Разбивает текст на куски размером chunk_size.

    Args:
        text: Исходный текст
        chunk_size: Размер куска в символах

    Returns:
        Список кусков текста
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        # Ищем последний пробел или перенос строки перед end
        if end < len(text):
            last_space = max(text.rfind(" ", start, end), text.rfind("\n", start, end))
            if last_space > start:
                end = last_space

        chunks.append(text[start:end].strip())
        start = end

    return chunks


def create_summary(
    text: str,
    llm_client,
    model_name: str,
    chunk_size: int = CHUNK_SIZE,
) -> dict:
    """
    Создаёт саммари текста через LLM.
    Для длинных текстов использует чанкование.

    Args:
        text: Исходный текст
        llm_client: LLM-клиент (OllamaClient и т.д.)
        model_name: Имя модели
        chunk_size: Размер куска для чанкования

    Returns:
        Словарь:
        {
            "summary": str,
            "tags": List[str],
            "topic": str,
            "is_controversial": bool,
            "error": Optional[str]
        }
    """
    logger.info(f"Создание саммари: text_length={len(text)}")

    try:
        # Чанкуем текст если нужно
        chunks = chunk_text(text, chunk_size)

        if len(chunks) == 1:
            # Короткий текст — одно саммари
            summary_text = chunks[0]
        else:
            # Длинный текст — саммари каждого куска
            logger.info(f"Чанкование: {len(chunks)} кусков")
            chunk_summaries = []
            for i, chunk in enumerate(chunks, 1):
                logger.info(f"Саммари куска {i}/{len(chunks)}")
                prompt = f"Кратко перескажи основной смысл этого текста (2-3 предложения):\n\n{chunk}"
                response = llm_client.generate(
                    prompt=prompt,
                    model=model_name,
                    temperature=0.3,
                    max_tokens=500,
                )
                if response and not response.startswith("[ОШИБКА]"):
                    chunk_summaries.append(response.strip())

            # Саммари саммари
            combined = "\n\n".join(chunk_summaries)
            if len(combined) > chunk_size:
                combined = combined[:chunk_size]

            summary_text = combined

        # Финальное саммари + теги + тематика
        prompt = f"""Проанализируй следующий текст и верни результат в формате:

ТЕМЫ: список ключевых тем через запятую (3-7 тем)
ТЕМАТИКА: одна из: Нейросети, Программирование, Инструменты, Дизайн, Безопасность, Другие тематики
СПОРНОЕ: да или нет (если текст содержит противоречивую или чувствительную информацию)

Текст:
{summary_text}

Ответ:"""

        response = llm_client.generate(
            prompt=prompt,
            model=model_name,
            temperature=0.3,
            max_tokens=500,
        )

        if not response or response.startswith("[ОШИБКА]"):
            logger.warning("LLM вернул ошибку при создании саммари")
            return {
                "summary": summary_text[:3000],
                "tags": [],
                "topic": "Другие тематики",
                "is_controversial": False,
                "error": "Не удалось получить саммари от LLM",
            }

        # Парсим ответ
        tags = []
        topic = "Другие тематики"
        is_controversial = False

        # Извлекаем темы
        tags_match = re.search(r"ТЕМЫ:\s*(.+?)(?:\n|$)", response, re.IGNORECASE)
        if tags_match:
            tags_str = tags_match.group(1).strip()
            tags = [t.strip() for t in tags_str.split(",") if t.strip()]

        # Извлекаем тематику
        topic_match = re.search(r"ТЕМАТИКА:\s*(.+?)(?:\n|$)", response, re.IGNORECASE)
        if topic_match:
            topic = topic_match.group(1).strip()

        # Извлекаем спорное
        controversial_match = re.search(r"СПОРНОЕ:\s*(да|нет)", response, re.IGNORECASE)
        if controversial_match:
            is_controversial = controversial_match.group(1).lower() == "да"

        # Ограничиваем длину саммари
        final_summary = summary_text[:3000]
        if len(summary_text) > 3000:
            final_summary = final_summary.rstrip() + "..."

        logger.info(
            f"Саммари создано: tags={len(tags)}, topic='{topic}', "
            f"controversial={is_controversial}"
        )

        return {
            "summary": final_summary,
            "tags": tags,
            "topic": topic,
            "is_controversial": is_controversial,
            "error": None,
        }

    except Exception as e:
        logger.error(f"Ошибка создания саммари: {e}")
        return {
            "summary": text[:3000] if len(text) > 3000 else text,
            "tags": [],
            "topic": "Другие тематики",
            "is_controversial": False,
            "error": str(e),
        }
