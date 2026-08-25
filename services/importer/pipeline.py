"""
services/importer/pipeline.py — оркестрация импорта контента.
"""

import asyncio
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Awaitable

from services.importer.downloaders import download_video_audio, fetch_article
from services.importer.transcribe import transcribe_audio
from services.importer.ocr import extract_text_from_image
from services.importer.summarizer import create_summary
from services.importer.notes import (
    check_duplicate,
    mark_as_processing,
    mark_as_done,
    add_new_tags,
    create_note,
    create_transcript_file,
    slugify,
)

logger = logging.getLogger("pumka.system")


async def import_url(
    url: str,
    vault_path: Path,
    data_dir: Path,
    llm_client,
    model_name: str,
    progress_callback: Optional[Callable[[str], Awaitable[None]]] = None,
) -> dict:
    """
    Импортирует контент по URL (видео или статья).

    Args:
        url: Ссылка на видео или статью
        vault_path: Путь к Obsidian Vault
        data_dir: Путь к data/
        llm_client: LLM-клиент
        model_name: Имя модели для саммари
        progress_callback: Функция для отправки прогресс-уведомлений

    Returns:
        Словарь с результатом:
        {
            "success": bool,
            "note_path": Optional[Path],
            "title": str,
            "summary": str,
            "error": Optional[str]
        }
    """
    logger.info(f"Начало импорта URL: {url}")

    # Проверяем дубликат
    duplicate = check_duplicate(data_dir, url=url)
    if duplicate:
        logger.info(f"Дубликат найден: {duplicate}")
        return {
            "success": False,
            "note_path": None,
            "title": duplicate.get("title", "Без названия"),
            "summary": f"Уже обработано {duplicate.get('date', '')}: {duplicate.get('note_path', '')}",
            "error": "duplicate",
        }

    # Помечаем как processing
    hash_key = mark_as_processing(data_dir, url=url, title="Обработка...")

    try:
        # Определяем тип контента (видео или статья)
        # Простая эвристика: если домен содержит youtube, vimeo и т.п. — видео
        video_domains = ["youtube.com", "youtu.be", "vimeo.com", "rutube.ru"]
        is_video = any(domain in url.lower() for domain in video_domains)

        if is_video:
            return await _import_video(
                url,
                vault_path,
                data_dir,
                llm_client,
                model_name,
                progress_callback,
                hash_key,
            )
        else:
            return await _import_article(
                url,
                vault_path,
                data_dir,
                llm_client,
                model_name,
                progress_callback,
                hash_key,
            )

    except Exception as e:
        logger.error(f"Ошибка импорта URL: {e}")
        _create_error_note(vault_path, url, str(e))
        return {
            "success": False,
            "note_path": None,
            "title": "Ошибка",
            "summary": "",
            "error": str(e),
        }


async def _import_video(
    url: str,
    vault_path: Path,
    data_dir: Path,
    llm_client,
    model_name: str,
    progress_callback: Optional[Callable[[str], Awaitable[None]]],
    hash_key: str,
) -> dict:
    """Импортирует видео (аудио + превью)."""

    # Прогресс: скачивание
    if progress_callback:
        await progress_callback("⏳ Скачиваю аудио и превью...")

    # Создаём временную папку для скачивания
    temp_dir = vault_path / "media" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Скачиваем аудио и превью
    video_data = await asyncio.to_thread(download_video_audio, url, temp_dir)

    if video_data.get("error"):
        logger.error(f"Ошибка скачивания видео: {video_data['error']}")
        _create_error_note(vault_path, url, video_data["error"])
        return {
            "success": False,
            "note_path": None,
            "title": "Ошибка скачивания",
            "summary": "",
            "error": video_data["error"],
        }

    title = video_data.get("title", "Без названия")
    audio_path = video_data.get("audio_path")
    thumbnail_path = video_data.get("thumbnail_path")

    if not audio_path:
        logger.error("Аудиофайл не найден после скачивания")
        _create_error_note(vault_path, url, "Аудиофайл не найден")
        return {
            "success": False,
            "note_path": None,
            "title": title,
            "summary": "",
            "error": "Аудиофайл не найден",
        }

    # Прогресс: транскрибация
    if progress_callback:
        duration_min = video_data.get("duration", 0) / 60
        await progress_callback(
            f"🎙 Транскрибирую (видео {duration_min:.1f} мин, может занять время)..."
        )

    # Транскрибация
    transcript_data = await asyncio.to_thread(transcribe_audio, audio_path)

    if transcript_data.get("error"):
        logger.error(f"Ошибка транскрибации: {transcript_data['error']}")
        _create_error_note(vault_path, url, transcript_data["error"])
        return {
            "success": False,
            "note_path": None,
            "title": title,
            "summary": "",
            "error": transcript_data["error"],
        }

    transcript_text = transcript_data.get("text", "")

    # Прогресс: саммари
    if progress_callback:
        await progress_callback("🧠 Суммаризирую...")

    # Саммари
    summary_data = await asyncio.to_thread(
        create_summary, transcript_text, llm_client, model_name
    )

    summary = summary_data.get("summary", "")
    tags = summary_data.get("tags", [])
    topic = summary_data.get("topic", "Другие тематики")

    # Прогресс: теги
    if progress_callback:
        await progress_callback("🏷 Подбираю теги...")

    # Добавляем новые теги
    add_new_tags(data_dir, tags)

    # Создаём файл транскрипта
    now = datetime.now()
    slug = slugify(title, now)
    transcript_path = create_transcript_file(vault_path, slug, transcript_text)

    # Копируем превью в media/previews/
    media_ref = None
    if thumbnail_path and thumbnail_path.exists():
        previews_dir = vault_path / "media" / "previews"
        previews_dir.mkdir(parents=True, exist_ok=True)
        dest_thumbnail = previews_dir / f"{slug}.jpg"
        shutil.copy2(thumbnail_path, dest_thumbnail)
        media_ref = f"media/previews/{slug}.jpg"

    # Создаём заметку
    note_path = create_note(
        vault_path=vault_path,
        data_dir=data_dir,
        title=title,
        source=url,
        content_type="video",
        topic=topic,
        tags=tags,
        summary=summary,
        transcript=f"media/documents/{slug}_transcript.md",
        media_path=media_ref,
    )

    # Помечаем как done
    mark_as_done(data_dir, hash_key, title, str(note_path))

    # Очищаем временные файлы
    try:
        if audio_path.exists():
            audio_path.unlink()
        if thumbnail_path and thumbnail_path.exists():
            thumbnail_path.unlink()
    except Exception as e:
        logger.warning(f"Не удалось удалить временные файлы: {e}")

    logger.info(f"Видео импортировано: {note_path}")

    return {
        "success": True,
        "note_path": note_path,
        "title": title,
        "summary": summary[:500],
        "topic": topic,
        "tags": tags,
        "error": None,
    }


async def _import_article(
    url: str,
    vault_path: Path,
    data_dir: Path,
    llm_client,
    model_name: str,
    progress_callback: Optional[Callable[[str], Awaitable[None]]],
    hash_key: str,
) -> dict:
    """Импортирует статью."""

    # Прогресс: скачивание
    if progress_callback:
        await progress_callback("⏳ Скачиваю статью...")

    # Скачиваем статью
    article_data = await asyncio.to_thread(fetch_article, url)

    if article_data.get("error"):
        logger.error(f"Ошибка скачивания статьи: {article_data['error']}")
        _create_error_note(vault_path, url, article_data["error"])
        return {
            "success": False,
            "note_path": None,
            "title": "Ошибка скачивания",
            "summary": "",
            "error": article_data["error"],
        }

    title = article_data.get("title", "Без названия")
    text = article_data.get("text", "")
    preview_url = article_data.get("preview_url")

    if not text or len(text.strip()) < 100:
        logger.error("Текст статьи пустой или слишком короткий")
        _create_error_note(vault_path, url, "Текст статьи пустой")
        return {
            "success": False,
            "note_path": None,
            "title": title,
            "summary": "",
            "error": "Текст статьи пустой",
        }

    # Прогресс: саммари
    if progress_callback:
        await progress_callback("🧠 Суммаризирую...")

    # Саммари
    summary_data = await asyncio.to_thread(create_summary, text, llm_client, model_name)

    summary = summary_data.get("summary", "")
    tags = summary_data.get("tags", [])
    topic = summary_data.get("topic", "Другие тематики")

    # Прогресс: теги
    if progress_callback:
        await progress_callback("🏷 Подбираю теги...")

    # Добавляем новые теги
    add_new_tags(data_dir, tags)

    # Создаём заметку
    note_path = create_note(
        vault_path=vault_path,
        data_dir=data_dir,
        title=title,
        source=url,
        content_type="article",
        topic=topic,
        tags=tags,
        summary=summary,
        transcript=None,
        media_path=preview_url,
    )

    # Помечаем как done
    mark_as_done(data_dir, hash_key, title, str(note_path))

    logger.info(f"Статья импортирована: {note_path}")

    return {
        "success": True,
        "note_path": note_path,
        "title": title,
        "summary": summary[:500],
        "topic": topic,
        "tags": tags,
        "error": None,
    }


async def import_photo(
    image_path: Path,
    caption: Optional[str],
    vault_path: Path,
    data_dir: Path,
    llm_client,
    model_name: str,
    progress_callback: Optional[Callable[[str], Awaitable[None]]] = None,
) -> dict:
    """
    Импортирует изображение (OCR + описание через LLM).
    """
    logger.info(f"Начало импорта изображения: {image_path}")

    # Проверяем дубликат
    with open(image_path, "rb") as f:
        content_hash = f.read()
    duplicate = check_duplicate(data_dir, content=content_hash.decode("latin-1"))
    if duplicate:
        return {
            "success": False,
            "note_path": None,
            "title": duplicate.get("title", "Без названия"),
            "summary": f"Уже обработано {duplicate.get('date', '')}",
            "error": "duplicate",
        }

    # Помечаем как processing
    hash_key = mark_as_processing(
        data_dir, content=content_hash.decode("latin-1"), title="Обработка..."
    )

    try:
        # Прогресс: OCR
        if progress_callback:
            await progress_callback("🔍 Распознаю текст на изображении...")

        # OCR
        ocr_data = await asyncio.to_thread(extract_text_from_image, image_path)
        ocr_text = ocr_data.get("text", "")

        # Копируем изображение в media/images/
        images_dir = vault_path / "media" / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        slug = slugify(caption or "image", now)
        dest_image = images_dir / f"{slug}{image_path.suffix}"
        shutil.copy2(image_path, dest_image)

        # Прогресс: описание
        if progress_callback:
            await progress_callback("🧠 Создаю описание...")

        # Описание через LLM
        if ocr_text:
            prompt = f"Опиши это изображение. Текст на изображении: {ocr_text}. Подпись пользователя: {caption or 'нет'}"
        else:
            prompt = f"Опиши это изображение. Подпись пользователя: {caption or 'нет'}. Текст на изображении не найден (OCR пуст)."

        description = await asyncio.to_thread(
            llm_client.generate,
            prompt=prompt,
            model=model_name,
            temperature=0.5,
            max_tokens=500,
        )

        if not description or description.startswith("[ОШИБКА]"):
            description = "Описание не удалось создать"

        # Прогресс: теги
        if progress_callback:
            await progress_callback("🏷 Подбираю теги...")

        # Теги и тематика
        tags_prompt = (
            f"Предложи 3-5 тегов для этого описания изображения: {description}"
        )
        tags_response = await asyncio.to_thread(
            llm_client.generate,
            prompt=tags_prompt,
            model=model_name,
            temperature=0.3,
            max_tokens=200,
        )

        tags = [t.strip() for t in tags_response.split(",") if t.strip()][:5]
        add_new_tags(data_dir, tags)

        # Создаём заметку
        title = caption or "Изображение"
        note_path = create_note(
            vault_path=vault_path,
            data_dir=data_dir,
            title=title,
            source="Telegram",
            content_type="photo",
            topic="Другие тематики",
            tags=tags,
            summary=description,
            transcript=None,
            media_path=f"media/images/{slug}{image_path.suffix}",
        )

        # Помечаем как done
        mark_as_done(data_dir, hash_key, title, str(note_path))

        logger.info(f"Изображение импортировано: {note_path}")

        return {
            "success": True,
            "note_path": note_path,
            "title": title,
            "summary": description[:500],
            "topic": "Другие тематики",
            "tags": tags,
            "error": None,
        }

    except Exception as e:
        logger.error(f"Ошибка импорта изображения: {e}")
        _create_error_note(vault_path, str(image_path), str(e))
        return {
            "success": False,
            "note_path": None,
            "title": "Ошибка",
            "summary": "",
            "error": str(e),
        }


async def import_text(
    text: str,
    vault_path: Path,
    data_dir: Path,
    llm_client,
    model_name: str,
    progress_callback: Optional[Callable[[str], Awaitable[None]]] = None,
) -> dict:
    """
    Импортирует текст (через /save).
    """
    logger.info(f"Начало импорта текста: {len(text)} символов")

    if len(text) < 20:
        return {
            "success": False,
            "note_path": None,
            "title": "Слишком коротко",
            "summary": "Минимум 20 символов",
            "error": "too_short",
        }

    # Проверяем дубликат
    duplicate = check_duplicate(data_dir, content=text)
    if duplicate:
        return {
            "success": False,
            "note_path": None,
            "title": duplicate.get("title", "Без названия"),
            "summary": f"Уже обработано {duplicate.get('date', '')}",
            "error": "duplicate",
        }

    # Помечаем как processing
    hash_key = mark_as_processing(data_dir, content=text, title="Обработка...")

    try:
        # Прогресс: саммари
        if progress_callback:
            await progress_callback("🧠 Суммаризирую...")

        # Саммари
        summary_data = await asyncio.to_thread(
            create_summary, text, llm_client, model_name
        )

        summary = summary_data.get("summary", "")
        tags = summary_data.get("tags", [])
        topic = summary_data.get("topic", "Другие тематики")

        # Прогресс: теги
        if progress_callback:
            await progress_callback("🏷 Подбираю теги...")

        # Добавляем новые теги
        add_new_tags(data_dir, tags)

        # Создаём заметку
        title = summary.split("\n")[0][:50] if summary else "Заметка"
        note_path = create_note(
            vault_path=vault_path,
            data_dir=data_dir,
            title=title,
            source="Telegram",
            content_type="text",
            topic=topic,
            tags=tags,
            summary=summary,
            transcript=None,
            media_path=None,
        )

        # Помечаем как done
        mark_as_done(data_dir, hash_key, title, str(note_path))

        logger.info(f"Текст импортирован: {note_path}")

        return {
            "success": True,
            "note_path": note_path,
            "title": title,
            "summary": summary[:500],
            "topic": topic,
            "tags": tags,
            "error": None,
        }

    except Exception as e:
        logger.error(f"Ошибка импорта текста: {e}")
        _create_error_note(vault_path, "text", str(e))
        return {
            "success": False,
            "note_path": None,
            "title": "Ошибка",
            "summary": "",
            "error": str(e),
        }


def _create_error_note(vault_path: Path, source: str, error: str) -> None:
    """Создаёт заметку об ошибке в Pumka/Проблемные."""
    try:
        problems_dir = vault_path / "Pumka" / "Проблемные"
        problems_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now()
        slug = slugify(f"error-{source[:30]}", now)
        error_note = problems_dir / f"{slug}.md"

        content = f"""# Ошибка обработки
- Дата: {now.strftime("%Y-%m-%d %H:%M")}
- Источник: {source}
- Ошибка: {error}

## Подробности
Обработка не удалась. Проверьте источник и попробуйте снова.
"""

        with open(error_note, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"Создана заметка об ошибке: {error_note}")

    except Exception as e:
        logger.error(f"Не удалось создать заметку об ошибке: {e}")
