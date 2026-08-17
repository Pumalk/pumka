#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pumka — мастер первого запуска (Этап 0).

Создаёт папки, конфиги и .env, ставит зависимости в venv,
проверяет связь с Ollama на хосте.

НЕ создаёт Git-репозиторий, НЕ скачивает модели, НЕ запускает полный health_check.
Повторный запуск: досоздаёт ТОЛЬКО недостающее, ничего не перезаписывает.
Проект — для личного использования автора.
"""

import secrets
import subprocess
import sys
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Исходное содержимое файлов
# ---------------------------------------------------------------------------

REQUIREMENTS = """pyyaml==6.0.2
python-dotenv==1.0.1
pydantic==2.10.6
httpx==0.28.1
redis==5.2.1
"""

PROTECTED_TERMS = """Pumka
Pumalk
Ollama
OpenRouter
RouterAI
Obsidian
Telegram
Hyper-V
XFCE
RDP
Redis
Docker
Kubernetes
Git
Python
JavaScript
TypeScript
VS Code
Claude
GPT
Gemini
Llama
Qwen
DeepSeek
Whisper
LLM
RAG
NLP
OCR
ffmpeg
yt-dlp
easyocr
Chroma
FastAPI
Pydantic
PyQt6
aiogram
"""

TRIGGERS_TEMPLATE = """# Pumka — триггеры (шаблон Этапа 0).
# Все расписания ВЫКЛЮЧЕНЫ. Заполняется на следующих этапах.

triggers:
  - name: "example"
    enabled: false
    schedule: ""
    description: "Пример-заготовка. Удалите или заполните на следующих этапах."
"""

CONFIG_TEMPLATE = """# Pumka — главный конфиг (создан мастером Этапа 0).
# Проект личный. Любые модели и пути меняются ЗДЕСЬ, без правки кода.

paths:
  project: "{project}"
  vault_path: "{vault}"
  projects: "{projects}"

llm:
  provider: "ollama"
  ollama_url: "http://{host_ip}:11434"
  default_profile: "power"
  profiles:
    power:
      light: "Qwen3-4B-Instruct-2507"
      medium: "DavidAU/Qwen3.5-9B"
      multimodal: "DavidAU/Qwen3.5-9B"
      coding: "nightmedia/Qwen3.5-9B-OmniCoder-Claude-Polaris"
      reasoning: "Jackrong/Qwen3.5-9B-Claude-4.6-Opus-Reasoning"
      critical: "amd/Llama-3.3-70B-Instruct-w4a16"
    balanced:
      light: "Qwen2.5-3B-Instruct-AWQ"
      medium: "Qwen3-4B-Instruct-2507"
      multimodal: "gemma-4-E4B-it-W4A16"
      coding: "Qwen2.5-Coder-3B-Instruct-AWQ"
      reasoning: "khazarai/Qwen3-4B-Kimi2.5-Reasoning-Distilled"
      critical: "Qwen3.5-9B"
    light:
      light: "Qwen2.5-1.5B-Instruct"
      medium: "Qwen2.5-3B-Instruct-AWQ"
      multimodal: "Qwen2.5-VL-3B-Instruct-AWQ"
      coding: "Qwen2.5-Coder-1.5B-Instruct"
      reasoning: "khazarai/Qwen3-4B-Kimi2.5-Reasoning-Distilled"
      critical: "Qwen3-4B-Instruct-2507"
  embedding: "ibm-granite/granite-embedding-311m-multilingual"
  embedding_fallback: "taide/embeddinggemma-GTAIDE-300m-2605"

security:
  allowed_paths:
    - "{project}"
    - "{vault}"
    - "{projects}"

max_queue_size: 200
"""

PROJECT_DIRS = [
    "core",
    "interfaces/telegram",
    "interfaces/gui",
    "agents/departments",
    "agents/builtin",
    "agents/custom",
    "recipes/builtin",
    "recipes/custom",
    "sandbox_tech/templates",
    "sandbox_dev/templates",
    "projects",
    "backups",
    "data/memory",
    "data/logs/tasks",
    "data/temp",
    "data/trash",
]

VAULT_DIRS = [
    "Нейросети",
    "Программирование",
    "Инструменты",
    "Дизайн",
    "Безопасность",
    "Другие тематики",
    "Pumka/Идеи для Pumka",
    "Pumka/Отклонённые идеи",
    "Pumka/Проблемые материалы",
    "Pumka/Спорные материалы",
    "media/images",
    "media/audio",
    "media/previews",
    "media/documents",
    "media/templates",
]

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def ask(question, default=""):
    """Один вопрос. Enter — значение по умолчанию."""
    if default:
        answer = input(f"{question} [{default}]: ").strip()
        return answer if answer else default
    return input(f"{question}: ").strip()


def ask_password(question):
    """Пароль с подтверждением."""
    while True:
        p1 = input(f"{question}: ").strip()
        if not p1:
            print("  Пустой пароль нельзя. Попробуйте ещё раз.")
            continue
        p2 = input("  Повторите пароль: ").strip()
        if p1 == p2:
            return p1
        print("  Пароли не совпадают. Попробуйте ещё раз.")


def make_dirs(base, rel_paths, report):
    """Создаёт папки, которых нет. Существующие не трогает."""
    for rel in rel_paths:
        target = base / rel
        if target.exists():
            report["skipped"].append(f"папка  {target}")
        else:
            target.mkdir(parents=True, exist_ok=True)
            report["created"].append(f"папка  {target}")


def write_if_missing(path, content, report):
    """Создаёт файл, только если его НЕТ. Никогда не перезаписывает."""
    if path.exists():
        report["skipped"].append(f"файл   {path.name}")
        return False
    path.write_text(content, encoding="utf-8")
    report["created"].append(f"файл   {path.name}")
    return True

# ---------------------------------------------------------------------------
# Основная логика
# ---------------------------------------------------------------------------

def main():
    print("=" * 62)
    print("  Pumka — мастер первого запуска (Этап 0)")
    print("  Личный проект автора. Отвечайте на вопросы по одному.")
    print("=" * 62)

    report = {"created": [], "skipped": []}

    # --- Вопросы визарда -------------------------------------------------
    default_project = str(Path(__file__).resolve().parent)
    project = Path(ask("\nВопрос 1/5. Путь к папке проекта", default_project)).expanduser()
    vault = Path(ask("Вопрос 2/5. Путь к Obsidian Vault", "~/obsidian_vault")).expanduser()
    projects = Path(ask("Вопрос 3/5. Путь к папке projects", str(project / "projects"))).expanduser()
    gui_password = ask_password("Вопрос 4/5. Пароль для будущего GUI")
    host_ip = ask("Вопрос 5/5. Локальный IP хоста, где работает Ollama", "192.168.0.63")

    # --- Папки -----------------------------------------------------------
    print("\n--- Создаю папки проекта и Vault ---")
    make_dirs(project, PROJECT_DIRS, report)
    make_dirs(vault, VAULT_DIRS, report)

    # --- Файлы -----------------------------------------------------------
    print("--- Создаю файлы (только недостающие) ---")
    write_if_missing(project / "requirements.txt", REQUIREMENTS, report)

    env_content = (
        "# Pumka — секреты и токены. Файл только для личного использования!\n"
        "TELEGRAM_BOT_TOKEN=\n"
        "TELEGRAM_GROUP_ID=\n"
        "OPENROUTER_API_KEY=\n"
        "ROUTERAI_API_KEY=\n"
        f"GUI_PASSWORD={gui_password}\n"
        f"DEVICE_TOKEN_KEY={secrets.token_hex(32)}\n"
    )
    write_if_missing(project / ".env", env_content, report)

    write_if_missing(
        project / "config.yaml",
        CONFIG_TEMPLATE.format(project=project, vault=vault,
                               projects=projects, host_ip=host_ip),
        report,
    )
    write_if_missing(project / "tags.json", '{\n  "tags": {}\n}\n', report)
    write_if_missing(project / "protected_terms.txt", PROTECTED_TERMS, report)
    write_if_missing(project / "triggers.yaml", TRIGGERS_TEMPLATE, report)

    # --- Зависимости ------------------------------------------------------
    print("\n--- Установка зависимостей в venv ---")
    print("Будут установлены пакеты:")
    for line in REQUIREMENTS.strip().splitlines():
        print("  -", line)
    answer = input("\nУстановить их сейчас? (y/n) [y]: ").strip().lower()
    if answer in ("", "y", "д"):
        subprocess.run([sys.executable, "-m", "pip", "install",
                        "-r", str(project / "requirements.txt")], check=True)
        report["created"].append("пакеты pip из requirements.txt")
    else:
        print("Пропускаю. Позже: pip install -r requirements.txt")

    # --- Проверка связи с Ollama ------------------------------------------
    print("\n--- Проверка связи ВМ -> Ollama ---")
    url = f"http://{host_ip}:11434/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            resp.read()
        print(f"ОК! Ollama отвечает по адресу {url}")
    except Exception:
        print("НЕ получилось связаться с Ollama. Проверьте на хосте (Windows):")
        print("  1. Ollama запущена (значок ламы в трее);")
        print("  2. переменная OLLAMA_HOST=0.0.0.0:11434 задана и Ollama перезапускалась после этого;")
        print("  3. в брандмауэре есть правило 'Ollama Local' для порта 11434 (профиль Private);")
        print(f"  4. IP хоста верный: {host_ip}")

    # --- Инструкция для хоста ----------------------------------------------
    print("\n--- Инструкция для хоста (Windows) ---")
    print("  1. Ollama: запущена и в автозагрузке (сделано на Этапе 0).")
    print("  2. Модели СЕЙЧАС НЕ скачиваем. Позже — вручную с хоста командой")
    print("     ollama pull <имя модели> по списку из config.yaml.")
    print("  3. Брандмауэр не отключаем: порт 11434 открыт только для частной сети.")

    # --- Итоговый отчёт -----------------------------------------------------
    print("\n" + "=" * 62)
    print("  ИТОГОВЫЙ ОТЧЁТ")
    print("=" * 62)
    print(f"Создано сейчас: {len(report['created'])}")
    for item in report["created"]:
        print("  [+] ", item)
    print(f"\nУже существовало (не тронуто): {len(report['skipped'])}")
    for item in report["skipped"]:
        print("  [=] ", item)
    print("\n--- Осталось сделать вручную ---")
    print("  1. SMB-шара Vault для Windows (задачи Д1-Д7) — следующим шагом.")
    print("  2. Git: коммит и тег etap-0-gotov — сразу после проверки.")
    print("  3. Telegram: создать бота у @BotFather и вписать токены в .env (позже).")
    print("  4. Модели на хосте: скачать по списку из config.yaml (позже).")
    print("\nГотово. Мастер завершил работу.")


if __name__ == "__main__":
    main()