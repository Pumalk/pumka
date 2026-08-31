"""
interfaces/telegram/jobs.py — функции-задачи для RQ-воркеров.
Задачи выполняются в отдельных процессах (не в процессе бота).
"""
import asyncio
import time
import logging
from pathlib import Path
from typing import Optional

from aiogram.types import ReactionTypeEmoji

from core.config import load_config
from core.ai_client import get_client
from interfaces.telegram.proxy import create_bot_auto
from services.importer.pipeline import import_url, import_photo, import_text


def _escape_markdown(text: str) -> str:
    """Экранирует спецсимволы Markdown, чтобы Telegram не падал на парсинге."""
    if not text:
        return ""
    # Экранируем: * _ ` [ ] ( ) ~ > # + - = | { } . !
    escape_chars = r"""\*_`[]()~>#+-=|{}.!"""
    return "".join("\\" + c if c in escape_chars else c for c in text)


logger = logging.getLogger("pumka.system")


# ============================================================================
# Тестовая задача (для проверки базового механизма)
# ============================================================================

def test_task(seconds: int = 3) -> str:
    """Тестовая задача для проверки работы RQ-очереди."""
    logger.info(f"test_task: начинаю, буду спать {seconds} сек")
    time.sleep(seconds)
    logger.info("test_task: завершаю")
    return f"Задача выполнена: спал {seconds} сек"


def enqueue_test_task(seconds: int = 3):
    """Ставит тестовую задачу в очередь. Вспомогательная функция для теста."""
    from redis import Redis
    from rq import Queue
    from interfaces.telegram.worker_runner import QUEUE_NAME, REDIS_HOST, REDIS_PORT

    redis_conn = Redis(host=REDIS_HOST, port=REDIS_PORT)
    queue = Queue(QUEUE_NAME, connection=redis_conn)
    job = queue.enqueue(test_task, seconds)
    return job.id


# ============================================================================
# Задача импорта контента по URL
# ============================================================================

def job_import_url(url: str, chat_id: int, message_id: int, source_description: str):
    """Задача импорта контента по URL (выполняется в воркере)."""
    logger.info(f"job_import_url: начинаю импорт {url}")
    asyncio.run(_job_import_url_async(url, chat_id, message_id, source_description))
    logger.info(f"job_import_url: завершил импорт {url}")


async def _job_import_url_async(url: str, chat_id: int, message_id: int, source_description: str):
    """Асинхронная реализация задачи импорта по URL."""
    config = load_config()

    transport = await create_bot_auto(config)
    if transport is None:
        logger.error("job_import_url: не удалось создать бота, задача прервана")
        return
    bot = transport.bot

    try:
        try:
            llm_client = get_client(config.llm.provider, ollama_url=config.llm.ollama_url)
        except ValueError as e:
            logger.error(f"job_import_url: ошибка конфигурации: {e}")
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Ошибка конфигурации: {e}",
                reply_to_message_id=message_id,
            )
            return

        profile = config.llm.profiles.get(config.llm.default_profile)
        model_name = (
            getattr(profile, "light", "Qwen2.5-3B-Instruct-AWQ")
            if profile
            else "Qwen2.5-3B-Instruct-AWQ"
        )

        async def progress_cb(text: str):
            try:
                await bot.send_message(
                    chat_id=chat_id, text=text, reply_to_message_id=message_id
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить прогресс: {e}")

        result = await import_url(
            url=url,
            vault_path=Path(config.paths.vault_path),
            data_dir=Path(config.paths.project) / "data",
            llm_client=llm_client,
            model_name=model_name,
            progress_callback=progress_cb,
        )

        if result["success"]:
            title_safe = _escape_markdown(result.get('title', 'Без названия'))
            topic_safe = _escape_markdown(result.get('topic', 'Другие тематики'))
            tags_safe = _escape_markdown(', '.join(result.get('tags', [])))
            summary_safe = _escape_markdown(result['summary'][:500])
            note_path_safe = _escape_markdown(str(result['note_path']))
            card = (
                f"✅ *Импортировано*\n"
                f"*{title_safe}*\n"
                f"Тематика: {topic_safe}\n"
                f"Теги: {tags_safe}\n"
                f"{summary_safe}...\n"
                f"📁 Заметка: `{note_path_safe}`"
            )
            await bot.send_message(
                chat_id=chat_id,
                text=card,
                parse_mode="Markdown",
                reply_to_message_id=message_id,
            )
            await asyncio.sleep(0.3)
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji="👍")],
                is_big=False,
            )
        elif result["error"] == "duplicate":
            await bot.send_message(
                chat_id=chat_id,
                text=f"ℹ️ {result['summary']}",
                reply_to_message_id=message_id,
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Не удалось обработать {url}. Подробности в «Проблемные материалы»",
                reply_to_message_id=message_id,
            )
            await asyncio.sleep(0.3)
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji="😱")],
                is_big=False,
            )
    except Exception as e:
        logger.error(f"job_import_url: непредвиденная ошибка: {e}")
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Задача не удалась: {source_description}. Подробности в «Проблемные материалы»",
                reply_to_message_id=message_id,
            )
            await asyncio.sleep(0.3)
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji="😱")],
                is_big=False,
            )
        except Exception as e2:
            logger.error(f"job_import_url: не удалось отправить ошибку: {e2}")
    finally:
        await bot.session.close()


# ============================================================================
# Задача импорта фото
# ============================================================================

def job_import_photo(
    image_path_str: str,
    caption: Optional[str],
    chat_id: int,
    message_id: int,
    source_description: str,
):
    """Задача импорта фото (выполняется в воркере)."""
    logger.info(f"job_import_photo: начинаю импорт {image_path_str}")
    asyncio.run(
        _job_import_photo_async(image_path_str, caption, chat_id, message_id, source_description)
    )
    logger.info(f"job_import_photo: завершил импорт {image_path_str}")


async def _job_import_photo_async(
    image_path_str: str,
    caption: Optional[str],
    chat_id: int,
    message_id: int,
    source_description: str,
):
    """Асинхронная реализация задачи импорта фото."""
    config = load_config()
    image_path = Path(image_path_str)

    transport = await create_bot_auto(config)
    if transport is None:
        logger.error("job_import_photo: не удалось создать бота, задача прервана")
        return
    bot = transport.bot

    try:
        try:
            llm_client = get_client(config.llm.provider, ollama_url=config.llm.ollama_url)
        except ValueError as e:
            logger.error(f"job_import_photo: ошибка конфигурации: {e}")
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Ошибка конфигурации: {e}",
                reply_to_message_id=message_id,
            )
            return

        profile = config.llm.profiles.get(config.llm.default_profile)
        model_name = (
            getattr(profile, "light", "Qwen2.5-3B-Instruct-AWQ")
            if profile
            else "Qwen2.5-3B-Instruct-AWQ"
        )

        async def progress_cb(text: str):
            try:
                await bot.send_message(
                    chat_id=chat_id, text=text, reply_to_message_id=message_id
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить прогресс: {e}")

        result = await import_photo(
            image_path=image_path,
            caption=caption,
            vault_path=Path(config.paths.vault_path),
            data_dir=Path(config.paths.project) / "data",
            llm_client=llm_client,
            model_name=model_name,
            progress_callback=progress_cb,
        )

        if result["success"]:
            title_safe = _escape_markdown(result.get('title', 'Без названия'))
            tags_safe = _escape_markdown(', '.join(result.get('tags', [])))
            summary_safe = _escape_markdown(result['summary'][:500])
            note_path_safe = _escape_markdown(str(result['note_path']))
            card = (
                f"✅ *Изображение импортировано*\n"
                f"*{title_safe}*\n"
                f"Теги: {tags_safe}\n"
                f"{summary_safe}...\n"
                f"📁 Заметка: `{note_path_safe}`"
            )
            await bot.send_message(
                chat_id=chat_id,
                text=card,
                parse_mode="Markdown",
                reply_to_message_id=message_id,
            )
            await asyncio.sleep(0.3)
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji="👍")],
                is_big=False,
            )
        elif result["error"] == "duplicate":
            await bot.send_message(
                chat_id=chat_id,
                text=f"ℹ️ {result['summary']}",
                reply_to_message_id=message_id,
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="⚠️ Не удалось обработать изображение. Подробности в «Проблемные материалы»",
                reply_to_message_id=message_id,
            )
            await asyncio.sleep(0.3)
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji="😱")],
                is_big=False,
            )
    except Exception as e:
        logger.error(f"job_import_photo: непредвиденная ошибка: {e}")
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Задача не удалась: {source_description}. Подробности в «Проблемные материалы»",
                reply_to_message_id=message_id,
            )
            await asyncio.sleep(0.3)
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji="😱")],
                is_big=False,
            )
        except Exception as e2:
            logger.error(f"job_import_photo: не удалось отправить ошибку: {e2}")
    finally:
        await bot.session.close()
        # Удаляем временный файл фото
        try:
            if image_path.exists():
                image_path.unlink()
        except Exception as e:
            logger.warning(f"Не удалось удалить временный файл: {e}")


# ============================================================================
# Задача импорта текста
# ============================================================================

def job_import_text(text: str, chat_id: int, message_id: int, source_description: str):
    """Задача импорта текста (выполняется в воркере)."""
    logger.info(f"job_import_text: начинаю импорт текста ({len(text)} символов)")
    asyncio.run(_job_import_text_async(text, chat_id, message_id, source_description))
    logger.info("job_import_text: завершил импорт текста")


async def _job_import_text_async(text: str, chat_id: int, message_id: int, source_description: str):
    """Асинхронная реализация задачи импорта текста."""
    config = load_config()

    transport = await create_bot_auto(config)
    if transport is None:
        logger.error("job_import_text: не удалось создать бота, задача прервана")
        return
    bot = transport.bot

    try:
        try:
            llm_client = get_client(config.llm.provider, ollama_url=config.llm.ollama_url)
        except ValueError as e:
            logger.error(f"job_import_text: ошибка конфигурации: {e}")
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Ошибка конфигурации: {e}",
                reply_to_message_id=message_id,
            )
            return

        profile = config.llm.profiles.get(config.llm.default_profile)
        model_name = (
            getattr(profile, "light", "Qwen2.5-3B-Instruct-AWQ")
            if profile
            else "Qwen2.5-3B-Instruct-AWQ"
        )

        async def progress_cb(text: str):
            try:
                await bot.send_message(
                    chat_id=chat_id, text=text, reply_to_message_id=message_id
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить прогресс: {e}")

        result = await import_text(
            text=text,
            vault_path=Path(config.paths.vault_path),
            data_dir=Path(config.paths.project) / "data",
            llm_client=llm_client,
            model_name=model_name,
            progress_callback=progress_cb,
        )

        if result["success"]:
            title_safe = _escape_markdown(result.get('title', 'Без названия'))
            topic_safe = _escape_markdown(result.get('topic', 'Другие тематики'))
            tags_safe = _escape_markdown(', '.join(result.get('tags', [])))
            summary_safe = _escape_markdown(result['summary'][:500])
            note_path_safe = _escape_markdown(str(result['note_path']))
            card = (
                f"✅ *Текст сохранён*\n"
                f"*{title_safe}*\n"
                f"Тематика: {topic_safe}\n"
                f"Теги: {tags_safe}\n"
                f"{summary_safe}...\n"
                f"📁 Заметка: `{note_path_safe}`"
            )
            await bot.send_message(
                chat_id=chat_id,
                text=card,
                parse_mode="Markdown",
                reply_to_message_id=message_id,
            )
            await asyncio.sleep(0.3)
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji="👍")],
                is_big=False,
            )
        elif result["error"] == "duplicate":
            await bot.send_message(
                chat_id=chat_id,
                text=f"ℹ️ {result['summary']}",
                reply_to_message_id=message_id,
            )
        elif result["error"] == "too_short":
            await bot.send_message(
                chat_id=chat_id,
                text="Слишком коротко для заметки (мин. 20 символов)",
                reply_to_message_id=message_id,
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="⚠️ Не удалось сохранить текст. Подробности в «Проблемные материалы»",
                reply_to_message_id=message_id,
            )
            await asyncio.sleep(0.3)
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji="😱")],
                is_big=False,
            )
    except Exception as e:
        logger.error(f"job_import_text: непредвиденная ошибка: {e}")
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Задача не удалась: {source_description}. Подробности в «Проблемные материалы»",
                reply_to_message_id=message_id,
            )
            await asyncio.sleep(0.3)
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji="😱")],
                is_big=False,
            )
        except Exception as e2:
            logger.error(f"job_import_text: не удалось отправить ошибку: {e2}")
    finally:
        await bot.session.close()


# ============================================================================
# Вспомогательные функции для постановки задач в очередь (Этап 6)
# ============================================================================

def is_source_in_queue(source: str) -> bool:
    """Проверяет, есть ли задача с таким источником в очереди или выполняется."""
    from redis import Redis
    from rq import Queue
    from rq.worker import Worker
    from interfaces.telegram.worker_runner import QUEUE_NAME, REDIS_HOST, REDIS_PORT

    redis_conn = Redis(host=REDIS_HOST, port=REDIS_PORT)
    queue = Queue(QUEUE_NAME, connection=redis_conn)

    # Проверяем задачи в очереди (ожидают выполнения)
    for job in queue.get_jobs():
        if job.meta and job.meta.get("источник") == source:
            return True

    # Проверяем задачи, которые выполняются сейчас
    for worker in Worker.all(connection=redis_conn):
        current_job = worker.get_current_job()
        if current_job and current_job.meta and current_job.meta.get("источник") == source:
            return True

    return False


def enqueue_import_url(url: str, chat_id: int, message_id: int) -> str:
    """Ставит задачу импорта по URL в очередь. Возвращает ID задачи."""
    from redis import Redis
    from rq import Queue
    from interfaces.telegram.worker_runner import QUEUE_NAME, REDIS_HOST, REDIS_PORT
    from datetime import datetime, timezone

    redis_conn = Redis(host=REDIS_HOST, port=REDIS_PORT)
    queue = Queue(QUEUE_NAME, connection=redis_conn)

    job = queue.enqueue(
        job_import_url,
        url, chat_id, message_id, url,
        meta={
            "тип": "ссылка",
            "источник": url,
            "chat_id": "",
            "номер_сообщения_в_телеграм": message_id,
            "время_постановки": datetime.now(timezone.utc).isoformat(),
        },
        job_timeout=None,
    )
    return job.id


def enqueue_import_photo(
    image_path_str: str,
    caption: Optional[str],
    chat_id: int,
    message_id: int,
    source: str,
) -> str:
    """Ставит задачу импорта фото в очередь. Возвращает ID задачи."""
    from redis import Redis
    from rq import Queue
    from interfaces.telegram.worker_runner import QUEUE_NAME, REDIS_HOST, REDIS_PORT
    from datetime import datetime, timezone

    redis_conn = Redis(host=REDIS_HOST, port=REDIS_PORT)
    queue = Queue(QUEUE_NAME, connection=redis_conn)

    job = queue.enqueue(
        job_import_photo,
        image_path_str, caption, chat_id, message_id, source,
        meta={
            "тип": "фото",
            "источник": source,
            "chat_id": "",
            "номер_сообщения_в_телеграм": message_id,
            "время_постановки": datetime.now(timezone.utc).isoformat(),
        },
        job_timeout=None,
    )
    return job.id


def enqueue_import_text(text: str, chat_id: int, message_id: int) -> str:
    """Ставит задачу импорта текста в очередь. Возвращает ID задачи."""
    from redis import Redis
    from rq import Queue
    from interfaces.telegram.worker_runner import QUEUE_NAME, REDIS_HOST, REDIS_PORT
    from datetime import datetime, timezone

    redis_conn = Redis(host=REDIS_HOST, port=REDIS_PORT)
    queue = Queue(QUEUE_NAME, connection=redis_conn)

    source = f"текст:{text[:100]}"
    job = queue.enqueue(
        job_import_text,
        text, chat_id, message_id, source,
        meta={
            "тип": "текст",
            "источник": source,
            "chat_id": "",
            "номер_сообщения_в_телеграм": message_id,
            "время_постановки": datetime.now(timezone.utc).isoformat(),
        },
        job_timeout=None,
    )
    return job.id


# ============================================================================
# Получение статуса очереди для /queue (Этап 6)
# ============================================================================

def get_queue_status() -> list:
    """
    Возвращает список активных задач (queued + started), до 10 штук.
    Формат элемента: {"status": "queued"|"started", "kind": str, "source": str, "seconds": int}
    """
    from datetime import datetime, timezone
    from redis import Redis
    from rq import Queue
    from rq.worker import Worker
    from interfaces.telegram.worker_runner import QUEUE_NAME, REDIS_HOST, REDIS_PORT

    redis_conn = Redis(host=REDIS_HOST, port=REDIS_PORT)
    queue = Queue(QUEUE_NAME, connection=redis_conn)
    now = datetime.now(timezone.utc)
    result = []

    # 1. Задачи в очереди (ожидают выполнения)
    try:
        for job in queue.get_jobs():
            meta = job.meta or {}
            enqueued_at = meta.get("время_постановки")
            seconds = 0
            if enqueued_at:
                try:
                    enqueued_dt = datetime.fromisoformat(enqueued_at)
                    seconds = int((now - enqueued_dt).total_seconds())
                except Exception:
                    pass
            result.append({
                "status": "queued",
                "kind": meta.get("тип", "?"),
                "source": meta.get("источник", "?"),
                "seconds": seconds,
            })
    except Exception as e:
        logger.warning(f"Не удалось получить задачи из очереди: {e}")

    # 2. Задачи, которые выполняются сейчас воркерами
    try:
        for worker in Worker.all(connection=redis_conn):
            current_job = worker.get_current_job()
            if current_job is None:
                continue
            meta = current_job.meta or {}
            enqueued_at = meta.get("время_постановки")
            seconds = 0
            if enqueued_at:
                try:
                    enqueued_dt = datetime.fromisoformat(enqueued_at)
                    seconds = int((now - enqueued_dt).total_seconds())
                except Exception:
                    pass
            result.append({
                "status": "started",
                "kind": meta.get("тип", "?"),
                "source": meta.get("источник", "?"),
                "seconds": seconds,
            })
    except Exception as e:
        logger.warning(f"Не удалось получить задачи от воркеров: {e}")

    # Ограничение до 10 элементов
    return result[:10]


def format_queue_status(items: list) -> str:
    """Форматирует список задач в читаемый текст для Telegram."""
    if not items:
        return "Очередь пуста"

    lines = ["📦 Очередь задач:"]
    for item in items:
        status_label = "выполняется" if item["status"] == "started" else "в очереди"
        source_short = item["source"][:60] + ("..." if len(item["source"]) > 60 else "")
        lines.append(f"• [{status_label}] {item['kind']}: {source_short} ({item['seconds']} сек)")
    return "\n".join(lines)
