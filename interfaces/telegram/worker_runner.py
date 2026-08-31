"""
interfaces/telegram/worker_runner.py — точка входа для RQ-воркеров.
Запуск: python -m interfaces.telegram.worker_runner
Стартует config.chat.max_parallel_tasks воркеров, слушающих очередь pumka_tasks.
"""
import logging
import sys
from multiprocessing import Process

from redis import Redis
from rq import Queue, Worker

from core.config import load_config
from core.logging_setup import setup_logging

logger = logging.getLogger("pumka.system")

QUEUE_NAME = "pumka_tasks"
REDIS_HOST = "localhost"
REDIS_PORT = 6379


def start_worker(queue_name: str, worker_id: int):
    """Запускает один RQ-воркер (выполняется в отдельном процессе)."""
    redis_conn = Redis(host=REDIS_HOST, port=REDIS_PORT)
    queue = Queue(queue_name, connection=redis_conn)
    worker = Worker([queue], connection=redis_conn, name=f"pumka-worker-{worker_id}")
    logger.info(f"Воркер {worker_id} начал работу")
    worker.work()


def main():
    config = load_config()
    setup_logging(config.logs_dir)

    num_workers = config.chat.max_parallel_tasks
    logger.info(f"Запуск {num_workers} RQ-воркеров для очереди '{QUEUE_NAME}'")
    print(f"🚀 Запуск {num_workers} RQ-воркеров для очереди '{QUEUE_NAME}'...")

    # Проверяем соединение с Redis
    try:
        test_conn = Redis(host=REDIS_HOST, port=REDIS_PORT)
        test_conn.ping()
        print("✅ Redis доступен")
    except Exception as e:
        print(f"[ОШИБКА] Не удалось подключиться к Redis на {REDIS_HOST}:{REDIS_PORT}: {e}")
        sys.exit(1)

    # Запускаем воркеров в отдельных процессах
    processes = []
    for i in range(num_workers):
        p = Process(target=start_worker, args=(QUEUE_NAME, i), daemon=True)
        p.start()
        processes.append(p)
        logger.info(f"Воркер {i+1}/{num_workers} запущен (PID: {p.pid})")

    print(f"✅ Все воркеры запущены. Нажмите Ctrl+C для остановки.")

    try:
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        print("\n⏹ Остановка воркеров...")
        for p in processes:
            p.terminate()
        for p in processes:
            p.join(timeout=5)
        print("✅ Все воркеры остановлены")


if __name__ == "__main__":
    main()
