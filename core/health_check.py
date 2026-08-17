"""
core/health_check.py — проверка здоровья системы Pumka.

Проверяет:
- Существование папок и права на запись
- Существование .env и config.yaml
- Связь с Redis
- Доступность Ollama
- Наличие скачанных моделей
- Заполненность токенов

Запуск: python -m core.health_check
"""

import os
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

from core.config import load_config
from core.logging_setup import setup_logging

logger = logging.getLogger("pumka.system")


# ============================================================================
# Структура результата проверки
# ============================================================================

class CheckResult:
    """Результат одной проверки."""
    
    def __init__(self, check: str, status: str, message: str):
        self.check = check
        self.status = status  # "ok", "error", "warning"
        self.message = message
    
    def to_dict(self) -> Dict[str, str]:
        return {
            "check": self.check,
            "status": self.status,
            "message": self.message
        }


class HealthCheckReport:
    """Полный отчёт проверки здоровья."""
    
    def __init__(self):
        self.errors: List[CheckResult] = []
        self.warnings: List[CheckResult] = []
        self.oks: List[CheckResult] = []
    
    def add_ok(self, check: str, message: str = "OK") -> None:
        self.oks.append(CheckResult(check, "ok", message))
    
    def add_error(self, check: str, message: str) -> None:
        self.errors.append(CheckResult(check, "error", message))
    
    def add_warning(self, check: str, message: str) -> None:
        self.warnings.append(CheckResult(check, "warning", message))
    
    def has_errors(self) -> bool:
        return len(self.errors) > 0
    
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0
    
    def to_dict(self) -> Dict[str, List[Dict[str, str]]]:
        return {
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
        }


# ============================================================================
# Проверки
# ============================================================================

def check_folders(config, report: HealthCheckReport) -> None:
    """Проверяет существование папок и права на запись."""
    
    folders = [
        ("data", config.paths.data),
        ("data/logs", config.paths.logs),
        ("data/memory", config.paths.memory),
        ("data/temp", config.paths.temp),
        ("data/trash", config.paths.trash),
        ("sandbox_tech", config.paths.sandbox_tech),
        ("sandbox_dev", config.paths.sandbox_dev),
        ("projects", config.paths.projects),
        ("backups", config.paths.backups),
    ]
    
    missing_folders = []
    no_write_folders = []
    
    for name, rel_path in folders:
        folder_path = config.project_root / rel_path
        
        # Проверяем существование
        if not folder_path.exists():
            missing_folders.append(name)
            continue
        
        # Проверяем права на запись
        if not os.access(folder_path, os.W_OK):
            no_write_folders.append(name)
    
    if missing_folders:
        report.add_error(
            "Папки",
            f"Отсутствуют папки: {', '.join(missing_folders)}. "
            f"Создайте их или запустите setup.py"
        )
    elif no_write_folders:
        report.add_error(
            "Папки",
            f"Нет прав на запись: {', '.join(no_write_folders)}. "
            f"Выполните: chmod u+w <папка>"
        )
    else:
        report.add_ok("Папки", f"Все {len(folders)} папок существуют и доступны для записи")


def check_config_files(config, report: HealthCheckReport) -> None:
    """Проверяет существование .env и config.yaml."""
    
    missing = []
    
    env_path = config.project_root / ".env"
    if not env_path.exists():
        missing.append(".env")
    
    config_path = config.project_root / "config.yaml"
    if not config_path.exists():
        missing.append("config.yaml")
    
    if missing:
        report.add_error(
            "Конфигурация",
            f"Отсутствуют файлы: {', '.join(missing)}"
        )
    else:
        report.add_ok("Конфигурация", ".env и config.yaml найдены")


def check_redis(config, report: HealthCheckReport) -> None:
    """Проверяет связь с Redis."""
    
    # Пробуем через Python-клиент
    try:
        import redis
        r = redis.Redis(host='localhost', port=6379, db=0, socket_timeout=3)
        r.ping()
        report.add_ok("Redis", "Подключение к Redis установлено")
        return
    except ImportError:
        logger.debug("redis python-пакет не установлен, пробуем через redis-cli")
    except Exception as e:
        report.add_error("Redis", f"Не удалось подключиться к Redis: {e}")
        return
    
    # Пробуем через redis-cli
    try:
        result = subprocess.run(
            ["redis-cli", "ping"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0 and result.stdout.strip() == "PONG":
            report.add_ok("Redis", "Подключение к Redis установлено (через redis-cli)")
        else:
            report.add_error("Redis", f"Redis не отвечает PONG: {result.stderr}")
    
    except FileNotFoundError:
        report.add_error("Redis", "redis-cli не найден. Установите Redis или python-пакет redis")
    except subprocess.TimeoutExpired:
        report.add_error("Redis", "Таймаут при подключении к Redis")
    except Exception as e:
        report.add_error("Redis", f"Ошибка при проверке Redis: {e}")


def check_ollama_connection(config, report: HealthCheckReport) -> None:
    """Проверяет доступность Ollama по URL из конфига."""
    
    ollama_url = config.llm.ollama_url
    tags_url = f"{ollama_url}/api/tags"
    
    try:
        req = Request(tags_url, method='GET')
        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            # Считаем количество моделей
            models = data.get("models", [])
            model_names = [m.get("name", "") for m in models]
            
            report.add_ok(
                "Ollama связь",
                f"Ollama доступен по адресу {ollama_url}. Моделей: {len(model_names)}"
            )
            
            # Сохраняем список моделей для дальнейших проверок
            report._ollama_models = model_names
    
    except HTTPError as e:
        report.add_error("Ollama связь", f"HTTP ошибка {e.code} при запросе к {tags_url}")
    except URLError as e:
        report.add_error(
            "Ollama связь",
            f"Не удалось подключиться к Ollama по адресу {ollama_url}. "
            f"Проверьте, что Ollama запущен и доступен по сети"
        )
    except Exception as e:
        report.add_error("Ollama связь", f"Ошибка при проверке Ollama: {e}")


def check_models(config, report: HealthCheckReport) -> None:
    """Проверяет наличие скачанных моделей для default_profile."""
    
    # Получаем список моделей из Ollama (если проверка связи прошла)
    available_models = getattr(report, '_ollama_models', None)
    
    if available_models is None:
        # Если проверка связи не прошла, пропускаем
        report.add_warning(
            "Модели",
            "Не удалось проверить модели — Ollama недоступен"
        )
        return
    
    # Получаем модели из default_profile
    profile_name = config.llm.default_profile
    profile = config.llm.profiles.get(profile_name)
    
    if not profile:
        report.add_warning(
            "Модели",
            f"Профиль '{profile_name}' не найден в конфигурации"
        )
        return
    
    # Список HF-имён моделей профиля
    profile_models = [
        ("light", profile.light),
        ("medium", profile.medium),
        ("multimodal", profile.multimodal),
        ("coding", profile.coding),
        ("reasoning", profile.reasoning),
        ("critical", profile.critical),
    ]
    
    # Проверяем каждую модель
    missing_models = []
    from core.llm_registry import resolve_ollama_name
    
    for tier, hf_name in profile_models:
        ollama_name = resolve_ollama_name(hf_name)
        
        # Проверяем, есть ли модель в списке скачанных
        # Ollama может возвращать имя с тегом или без, проверяем по вхождению
        found = any(ollama_name in m or m in ollama_name for m in available_models)
        
        if not found:
            missing_models.append((tier, hf_name, ollama_name))
    
    if missing_models:
        messages = []
        for tier, hf_name, ollama_name in missing_models:
            messages.append(f"  {tier}: {hf_name} → {ollama_name}")
        
        report.add_warning(
            "Модели",
            f"{len(missing_models)} из {len(profile_models)} моделей не скачаны:\n" +
            "\n".join(messages) +
            "\nСкачайте: ollama pull <имя_модели>"
        )
    else:
        report.add_ok("Модели", f"Все {len(profile_models)} моделей профиля '{profile_name}' скачаны")


def check_tokens(config, report: HealthCheckReport) -> None:
    """Проверяет заполненность токенов в .env."""
    
    empty_tokens = []
    
    if not config.telegram.token:
        empty_tokens.append("TELEGRAM_BOT_TOKEN")
    
    if not config.telegram.group_id:
        empty_tokens.append("TELEGRAM_GROUP_ID")
    
    # OpenRouter и RouterAI — заглушки, но проверяем ключи
    # Они могут быть пустыми — это не ошибка, а предупреждение
    
    if empty_tokens:
        report.add_warning(
            "Токены",
            f"Не заполнены токены в .env: {', '.join(empty_tokens)}. "
            f"Telegram-бот не будет работать, пока токены не заданы."
        )
    else:
        report.add_ok("Токены", "Все токены заполнены")


# ============================================================================
# Главная функция проверки
# ============================================================================

def run_health_check() -> HealthCheckReport:
    """
    Запускает все проверки здоровья.
    
    Returns:
        HealthCheckReport с результатами
    """
    config = load_config()
    report = HealthCheckReport()
    
    # Проверки-ошибки (критичные)
    check_folders(config, report)
    check_config_files(config, report)
    check_redis(config, report)
    check_ollama_connection(config, report)
    
    # Проверки-предупреждения (не критичные)
    check_models(config, report)
    check_tokens(config, report)
    
    return report


# ============================================================================
# Вывод отчёта в консоль
# ============================================================================

def print_health_report(report: HealthCheckReport) -> None:
    """Выводит отчёт проверки в консоль."""
    
    print("🏥 Проверка здоровья Pumka:")
    print("=" * 50)
    
    # OK
    for check in report.oks:
        print(f"✅ {check.check}: {check.message}")
    
    # Ошибки
    for check in report.errors:
        print(f"❌ {check.check}: {check.message}")
    
    # Предупреждения
    for check in report.warnings:
        print(f"⚠️  {check.check}: {check.message}")
    
    print("=" * 50)
    
    # Итог
    if report.has_errors():
        print(f"❌ Результат: {len(report.errors)} ошибок, {len(report.warnings)} предупреждений")
        print("   Система НЕ может запуститься. Исправьте ошибки выше.")
    elif report.has_warnings():
        print(f"⚠️  Результат: {len(report.warnings)} предупреждений")
        print("   Система может запуститься, но некоторые функции ограничены.")
    else:
        print("✅ Результат: Все проверки пройдены успешно")
        print("   Система полностью готова к работе.")


# ============================================================================
# Точка входа: python -m core.health_check
# ============================================================================

if __name__ == "__main__":
    from core.logging_setup import setup_logging
    
    config = load_config()
    setup_logging(config.logs_dir)
    
    report = run_health_check()
    print_health_report(report)
    
    # Выход с кодом ошибки если есть критичные проблемы
    import sys
    sys.exit(1 if report.has_errors() else 0)