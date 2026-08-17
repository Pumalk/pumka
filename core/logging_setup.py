"""
core/logging_setup.py — настройка логирования для Pumka.

Все логи пишутся в файлы в data/logs/, не в stdout.
"""

import logging
import sys
from pathlib import Path
from typing import Optional


def setup_logging(logs_dir: Path) -> None:
    """
    Настраивает логирование в файлы.
    
    Создаёт два логгера:
    - pumka.actions: для записи действий инструментов
    - pumka.incidents: для ошибок безопасности
    - pumka.system: для системных сообщений
    """
    
    # Создаём папку logs, если её нет
    logs_dir.mkdir(parents=True, exist_ok=True)
    
    # Формат логов
    formatter = logging.Formatter(
        fmt='%(asctime)s | %(name)-15s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # === Логгер действий (actions.log) ===
    actions_logger = logging.getLogger("pumka.actions")
    actions_logger.setLevel(logging.INFO)
    actions_logger.propagate = False  # не дублировать в stdout
    
    actions_handler = logging.FileHandler(
        logs_dir / "actions.log",
        encoding='utf-8'
    )
    actions_handler.setFormatter(formatter)
    actions_logger.addHandler(actions_handler)
    
    # === Логгер инцидентов (incidents.log) ===
    incidents_logger = logging.getLogger("pumka.incidents")
    incidents_logger.setLevel(logging.WARNING)
    incidents_logger.propagate = False
    
    incidents_handler = logging.FileHandler(
        logs_dir / "incidents.log",
        encoding='utf-8'
    )
    incidents_handler.setFormatter(formatter)
    incidents_logger.addHandler(incidents_handler)
    
    # === Системный логгер (system.log) ===
    system_logger = logging.getLogger("pumka.system")
    system_logger.setLevel(logging.INFO)
    system_logger.propagate = False
    
    system_handler = logging.FileHandler(
        logs_dir / "system.log",
        encoding='utf-8'
    )
    system_handler.setFormatter(formatter)
    system_logger.addHandler(system_handler)


def get_logger(name: str) -> logging.Logger:
    """
    Возвращает логгер с указанным именем.
    
    Примеры:
        logger = get_logger("pumka.actions")
        logger.info("Инструмент read_file вызван")
    """
    return logging.getLogger(name)