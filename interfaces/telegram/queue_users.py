"""
interfaces/telegram/queue_users.py — очередь сообщений per-user и воркеры.
Только механика: очереди, воркеры, очистка.
Функцию обработки передают снаружи как callback (process_fn).
"""
import asyncio
import logging
from typing import Dict, Callable

logger = logging.getLogger("pumka.system")

# Очереди и воркеры для обработки сообщений per-user
user_queues: Dict[int, asyncio.Queue] = {}
user_workers: Dict[int, asyncio.Task] = {}


async def user_queue_worker(
    user_id: int,
    process_fn: Callable,
    *args,
    **kwargs,
):
    """
    Воркер для обработки очереди сообщений одного пользователя.
    Достаёт сообщения из очереди и вызывает process_fn.
    process_fn — функция обработки (передаётся извне, чтобы не было циклического импорта).
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
            try:
                await process_fn(message, is_old, age_seconds, *args, **kwargs)
            except Exception as e:
                logger.error(f"Ошибка при обработке сообщения: {e}")
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


def ensure_worker(user_id: int, process_fn: Callable, *args, **kwargs) -> None:
    """
    Создаёт очередь и воркер для пользователя, если их нет или воркер завершён.
    """
    if user_id not in user_queues:
        user_queues[user_id] = asyncio.Queue()
        logger.info(f"Создана очередь для пользователя {user_id}")
    if user_id not in user_workers or user_workers[user_id].done():
        task = asyncio.create_task(
            user_queue_worker(user_id, process_fn, *args, **kwargs)
        )
        user_workers[user_id] = task
        logger.info(f"Запущен воркер для пользователя {user_id}")


def clear_user_queue(user_id: int) -> int:
    """
    Очищает очередь сообщений пользователя.
    Возвращает количество удалённых сообщений.
    """
    queue = user_queues.get(user_id)
    if queue is None:
        return 0
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
            f"Очередь очищена: убрано сообщений={cleared} для {user_id}"
        )
    return cleared
