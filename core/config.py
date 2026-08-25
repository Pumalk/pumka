"""
core/config.py — загрузка и валидация конфигурации Pumka.

Загружает переменные окружения из .env и основную конфигурацию из config.yaml.
Валидирует структуру через Pydantic-модели.
Все ошибки — на русском языке, с понятными инструкциями.
"""

import os
import sys
from pathlib import Path
from typing import Optional, Dict, Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, ValidationError


# ============================================================================
# Pydantic-модели для валидации config.yaml
# ============================================================================


class LLMProfile(BaseModel):
    """Профиль LLM с 6 уровнями (tiers)."""

    light: str
    medium: str
    multimodal: str
    coding: str
    reasoning: str
    critical: str


class LLMConfig(BaseModel):
    """Конфигурация LLM-провайдеров."""

    provider: Literal["ollama", "openrouter", "routerai"] = "ollama"
    ollama_url: str = "http://localhost:11434"
    default_profile: Literal["power", "balanced", "light"] = "balanced"
    profiles: Dict[Literal["power", "balanced", "light"], LLMProfile]
    embedding: str = ""
    embedding_fallback: str = ""


class PathsConfig(BaseModel):
    """Пути к важным папкам проекта."""

    project: str = "/home/pumka/Pumka"
    vault_path: str = "/home/pumka/obsidian_vault"
    projects: str = "/home/pumka/Pumka/projects"
    data: str = "data"
    logs: str = "data/logs"
    memory: str = "data/memory"
    temp: str = "data/temp"
    trash: str = "data/trash"
    sandbox_tech: str = "sandbox_tech"
    sandbox_dev: str = "sandbox_dev"
    projects: str = "projects"
    backups: str = "backups"


class SecurityConfig(BaseModel):
    """Настройки безопасности."""

    allowed_paths: list[str] = Field(
        default_factory=lambda: [
            "/home/pumka/Pumka/data",
            "/home/pumka/Pumka/projects",
            "/home/pumka/Pumka/sandbox_tech",
            "/home/pumka/Pumka/sandbox_dev",
        ]
    )
    max_file_size_mb: int = 10


class TelegramConfig(BaseModel):
    """Настройки Telegram-бота."""

    token: str = ""
    group_id: str = ""
    allowed_user_id: Optional[int] = None
    proxy: str = ""
    proxy_auto: bool = False
    proxy_api_url: str = "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=all&ssl=all&anonymity=all"
    proxy_use_doh: bool = True
    proxy_doh_url: str = "https://cloudflare-dns.com/dns-query"
    proxy_select_best: bool = True


class GUIConfig(BaseModel):
    """Настройки GUI."""

    password: str = ""
    device_token_key: str = ""


class AgentsConfig(BaseModel):
    """Настройки агентов."""

    max_parallel_tasks: int = 3
    task_timeout_seconds: int = 300


class ConfigData(BaseModel):
    """Полная структура config.yaml."""

    llm: LLMConfig
    paths: PathsConfig = Field(default_factory=PathsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    gui: GUIConfig = Field(default_factory=GUIConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)


# ============================================================================
# Объект Config (результат загрузки)
# ============================================================================


class Config:
    """
    Удобный объект конфигурации.
    Атрибуты: paths, llm, security, telegram, gui, agents, project_root
    """

    def __init__(
        self,
        project_root: Path,
        llm: LLMConfig,
        paths: PathsConfig,
        security: SecurityConfig,
        telegram: TelegramConfig,
        gui: GUIConfig,
        agents: AgentsConfig,
    ):
        self.project_root = project_root
        self.llm = llm
        self.paths = paths
        self.security = security
        self.telegram = telegram
        self.gui = gui
        self.agents = agents

        # Абсолютные пути для удобстваства
        self.data_dir = project_root / paths.data
        self.logs_dir = project_root / paths.logs
        self.memory_dir = project_root / paths.memory
        self.temp_dir = project_root / paths.temp
        self.trash_dir = project_root / paths.trash

    def __repr__(self) -> str:
        return (
            f"Config(provider={self.llm.provider!r}, "
            f"profile={self.llm.default_profile!r}, "
            f"root={self.project_root})"
        )


# ============================================================================
# Функции загрузки
# ============================================================================


def _find_project_root() -> Path:
    """
    Ищет корень проекта по наличию config.yaml.
    Поднимается вверх от текущей директории, пока не найдёт.
    """
    current = Path.cwd().resolve()

    # Поднимаемся максимум на 5 уровней вверх
    for _ in range(5):
        if (current / "config.yaml").exists():
            return current
        parent = current.parent
        if parent == current:  # дошли до корня файловой системы
            break
        current = parent

    # Если не нашли — используем текущую директорию
    # (config.yaml может быть не создан, это ошибка, но обработаем позже)
    return Path.cwd().resolve()


def _load_env_file(env_path: Path) -> Dict[str, str]:
    """
    Загружает переменные из .env файла.
    Возвращает словарь значений. Пустые значения возвращаются как пустые строки.
    """
    env_vars: Dict[str, str] = {}

    if not env_path.exists():
        print(f"[ПРЕДУПРЕЖДЕНИЕ] Файл .env не найден в {env_path.parent}")
        return env_vars

    # python-dotenv может читать напрямую
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                env_vars[key] = value
                # Также устанавливаем в os.environ для совместимости
                os.environ[key] = value

    return env_vars


def _load_yaml_config(yaml_path: Path) -> Dict[str, Any]:
    """Загружает и парсит config.yaml."""
    if not yaml_path.exists():
        print(f"[ОШИБКА] Файл конфигурации не найден: {yaml_path}")
        print("        Запустите setup.py для создания config.yaml")
        sys.exit(1)

    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if data is None:
            print(f"[ОШИБКА] Файл {yaml_path} пустой или содержит некорректный YAML")
            sys.exit(1)

        return data

    except yaml.YAMLError as e:
        print(f"[ОШИБКА] Некорректный YAML в {yaml_path}")
        print(f"        {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[ОШИБКА] Не удалось прочитать {yaml_path}")
        print(f"        {e}")
        sys.exit(1)


def _merge_env_to_config(
    config_data: Dict[str, Any], env_vars: Dict[str, str]
) -> Dict[str, Any]:
    """
    Объединяет переменные из .env с конфигурацией.
    Значения из .env имеют приоритет над значениями из config.yaml
    для секций telegram и gui.
    """

    # Telegram
    if "telegram" not in config_data:
        config_data["telegram"] = {}
    if "TELEGRAM_BOT_TOKEN" in env_vars:
        config_data["telegram"]["token"] = env_vars["TELEGRAM_BOT_TOKEN"]
    if "TELEGRAM_GROUP_ID" in env_vars:
        config_data["telegram"]["group_id"] = env_vars["TELEGRAM_GROUP_ID"]
    if "TELEGRAM_ALLOWED_USER_ID" in env_vars and env_vars["TELEGRAM_ALLOWED_USER_ID"]:
        try:
            config_data["telegram"]["allowed_user_id"] = int(
                env_vars["TELEGRAM_ALLOWED_USER_ID"]
            )
        except ValueError:
            print("[ОШИБКА] TELEGRAM_ALLOWED_USER_ID должен быть числом")
            sys.exit(1)
        if "TELEGRAM_PROXY" in env_vars:
            config_data["telegram"]["proxy"] = env_vars["TELEGRAM_PROXY"]
        if "TELEGRAM_PROXY_AUTO" in env_vars:
            config_data["telegram"]["proxy_auto"] = env_vars[
                "TELEGRAM_PROXY_AUTO"
            ].lower() in ("true", "1", "yes", "да")
        if "TELEGRAM_PROXY_API_URL" in env_vars:
            config_data["telegram"]["proxy_api_url"] = env_vars[
                "TELEGRAM_PROXY_API_URL"
            ]
        if "TELEGRAM_PROXY_USE_DOH" in env_vars:
            config_data["telegram"]["proxy_use_doh"] = env_vars[
                "TELEGRAM_PROXY_USE_DOH"
            ].lower() in ("true", "1", "yes", "да")
        if "TELEGRAM_PROXY_DOH_URL" in env_vars:
            config_data["telegram"]["proxy_doh_url"] = env_vars[
                "TELEGRAM_PROXY_DOH_URL"
            ]
        if "TELEGRAM_PROXY_SELECT_BEST" in env_vars:
            config_data["telegram"]["proxy_select_best"] = env_vars[
                "TELEGRAM_PROXY_SELECT_BEST"
            ].lower() in ("true", "1", "yes", "да")

    # GUI
    if "gui" not in config_data:
        config_data["gui"] = {}

    if "GUI_PASSWORD" in env_vars:
        config_data["gui"]["password"] = env_vars["GUI_PASSWORD"]
    if "DEVICE_TOKEN_KEY" in env_vars:
        config_data["gui"]["device_token_key"] = env_vars["DEVICE_TOKEN_KEY"]

    return config_data


def load_config(project_root: Optional[Path] = None) -> Config:
    """
    Главная точка входа. Загружает всю конфигурацию и возвращает объект Config.

    Args:
        project_root: Путь к корню проекта. Если None — ищется автоматически.

    Returns:
        Объект Config с валидированными данными.

    Raises:
        SystemExit: При критических ошибках конфигурации.
    """

    # 1. Определяем корень проекта
    if project_root is None:
        project_root = _find_project_root()

    # 2. Загружаем .env
    env_path = project_root / ".env"
    env_vars = _load_env_file(env_path)

    # 3. Загружаем config.yaml
    yaml_path = project_root / "config.yaml"
    config_data = _load_yaml_config(yaml_path)

    # 4. Объединяем .env с YAML (env имеет приоритет для секретов)
    config_data = _merge_env_to_config(config_data, env_vars)

    # 5. Валидируем через Pydantic
    try:
        validated = ConfigData(**config_data)
    except ValidationError as e:
        print("[ОШИБКА] Некорректная структура config.yaml:")
        print()
        for error in e.errors():
            path = " → ".join(str(p) for p in error["loc"])
            msg = error["msg"]
            print(f"  Поле: {path}")
            print(f"  Проблема: {msg}")
            print()
        print("Исправьте ошибки в config.yaml и попробуйте снова.")
        sys.exit(1)
    except Exception as e:
        print(f"[ОШИБКА] Неожиданная ошибка при валидации конфигурации:")
        print(f"        {e}")
        sys.exit(1)

    # 6. Создаём и возвращаем объект Config
    config = Config(
        project_root=project_root,
        llm=validated.llm,
        paths=validated.paths,
        security=validated.security,
        telegram=validated.telegram,
        gui=validated.gui,
        agents=validated.agents,
    )

    return config


# ============================================================================
# Точка входа для тестирования: python -m core.config
# ============================================================================

if __name__ == "__main__":
    print("Загрузка конфигурации Pumka...")
    print()

    config = load_config()

    print(f"✅ Конфигурация загружена успешно")
    print(f"   Корень проекта: {config.project_root}")
    print(f"   Провайдер LLM: {config.llm.provider}")
    print(f"   URL Ollama: {config.llm.ollama_url}")
    print(f"   Профиль по умолчанию: {config.llm.default_profile}")
    print(f"   Папка логов: {config.logs_dir}")
    print(f"   Telegram токен: {'задан' if config.telegram.token else 'НЕ задан'}")
    print()

    # Показываем профиль
    profile_name = config.llm.default_profile
    profile = config.llm.profiles[profile_name]
    print(f"   Модели профиля '{profile_name}':")
    print(f"     light:     {profile.light}")
    print(f"     medium:    {profile.medium}")
    print(f"     multimodal:{profile.multimodal}")
    print(f"     coding:    {profile.coding}")
    print(f"     reasoning: {profile.reasoning}")
    print(f"     critical:  {profile.critical}")
