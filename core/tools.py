"""
core/tools.py — реестр инструментов Pumka.

Содержит:
- ToolRegistry — реестр для регистрации и выполнения инструментов
- 4 базовых инструмента: read_file, write_file, list_directory, echo
- Проверка белого списка путей для файловых операций
- Логирование всех действий в data/logs/actions.log
- Логирование инцидентов безопасности в data/logs/incidents.log
"""

import os
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, get_type_hints
from functools import wraps

logger = logging.getLogger("pumka.system")
actions_logger = logging.getLogger("pumka.actions")
incidents_logger = logging.getLogger("pumka.incidents")


# ============================================================================
# Реестр инструментов
# ============================================================================

class ToolRegistry:
    """Реестр инструментов Pumka."""
    
    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}
    
    def register(
        self,
        name: str,
        description: str,
        func: Callable,
        parameters_schema: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Регистрирует инструмент в реестре.
        
        Args:
            name: Имя инструмента (латиница, snake_case)
            description: Описание для LLM
            func: Python-функция, выполняющая действие
            parameters_schema: JSON Schema параметров (опционально)
        """
        if name in self._tools:
            logger.warning(f"Инструмент '{name}' уже зарегистрирован, перезаписываю")
        
        self._tools[name] = {
            "name": name,
            "description": description,
            "func": func,
            "parameters_schema": parameters_schema or {}
        }
        logger.info(f"Зарегистрирован инструмент: {name}")
    
    def get(self, name: str) -> Optional[Dict[str, Any]]:
        """Возвращает информацию об инструменте."""
        return self._tools.get(name)
    
    def list_tools(self) -> List[str]:
        """Возвращает список имён всех зарегистрированных инструментов."""
        return list(self._tools.keys())
    
    def get_openai_tools_format(self) -> List[Dict[str, Any]]:
        """
        Возвращает список инструментов в формате OpenAI tools.
        Используется для передачи в LLM.
        """
        result = []
        for name, tool in self._tools.items():
            result.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool["description"],
                    "parameters": tool["parameters_schema"]
                }
            })
        return result
    
    def execute(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Выполняет инструмент с указанными параметрами.
        
        Returns:
            {"success": bool, "result": Any, "error": Optional[str]}
        """
        tool = self._tools.get(tool_name)
        
        if tool is None:
            error_msg = f"Инструмент '{tool_name}' не найден в реестре"
            actions_logger.error(f"Исполнение: {tool_name} | Параметры: {params} | Результат: ОШИБКА - {error_msg}")
            return {"success": False, "result": None, "error": error_msg}
        
        try:
            result = tool["func"](**params)
            actions_logger.info(f"Исполнение: {tool_name} | Параметры: {params} | Результат: УСПЕХ")
            return {"success": True, "result": result, "error": None}
        
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            actions_logger.error(f"Исполнение: {tool_name} | Параметры: {params} | Результат: ОШИБКА - {error_msg}")
            return {"success": False, "result": None, "error": error_msg}


# ============================================================================
# Безопасность: проверка путей
# ============================================================================

def _is_path_allowed(path: str, allowed_paths: List[str]) -> bool:
    """
    Проверяет, разрешён ли путь белым списком.
    
    Args:
        path: Путь для проверки
        allowed_paths: Список разрешённых путей (из config.security.allowed_paths)
    
    Returns:
        True если путь разрешён, False иначе
    """
    # Нормализуем путь (убираем .., . и символические ссылки)
    try:
        normalized = os.path.realpath(os.path.expanduser(path))
    except (ValueError, OSError) as e:
        incidents_logger.warning(f"Не удалось нормализовать путь '{path}': {e}")
        return False
    
    # Проверяем каждый разрешённый путь
    for allowed in allowed_paths:
        try:
            allowed_normalized = os.path.realpath(os.path.expanduser(allowed))
            # Путь разрешён, если он начинается с разрешённого пути
            if normalized.startswith(allowed_normalized + os.sep) or normalized == allowed_normalized:
                return True
        except (ValueError, OSError):
            continue
    
    return False


def _contains_env_filename(path: str) -> bool:
    """
    Проверяет, содержит ли путь файл .env или похожее имя.
    
    Args:
        path: Путь для проверки
    
    Returns:
        True если путь содержит .env
    """
    path_lower = path.lower()
    basename = os.path.basename(path_lower)
    
    # Проверяем имя файла
    if basename == ".env" or basename.endswith(".env"):
        return True
    
    # Проверяем, есть ли .env где-то в пути
    if ".env" in path_lower:
        return True
    
    return False


# ============================================================================
# Базовые инструменты
# ============================================================================

def create_file_tools(allowed_paths: List[str]) -> Dict[str, Callable]:
    """
    Создаёт файловые инструменты с проверкой белого списка.
    
    Args:
        allowed_paths: Список разрешённых путей из конфига
    
    Returns:
        Словарь с функциями инструментов
    """
    
    def read_file(path: str) -> str:
        """
        Читает содержимое файла.
        
        Args:
            path: Путь к файлу
        
        Returns:
            Содержимое файла как строка
        
        Raises:
            PermissionError: Если доступ запрещён
            FileNotFoundError: Если файл не найден
            ValueError: Если файл .env
        """
        # Жёсткая блокировка .env
        if _contains_env_filename(path):
            incidents_logger.warning(
                f"ПОПЫТКА ЧТЕНИЯ .env: {path} | Заблокировано"
            )
            raise PermissionError(
                f"Чтение файлов .env запрещено правилами безопасности"
            )
        
        # Проверка белого списка
        if not _is_path_allowed(path, allowed_paths):
            incidents_logger.warning(
                f"ПОПЫТКА ВЫХОДА ЗА ПРЕДЕЛЫ: read_file('{path}') | "
                f"Разрешённые пути: {allowed_paths}"
            )
            raise PermissionError(
                f"Путь '{path}' не входит в список разрешённых. "
                f"Разрешённые папки: {', '.join(allowed_paths)}"
            )
        
        # Проверка существования
        if not os.path.exists(path):
            raise FileNotFoundError(f"Файл не найден: {path}")
        
        if not os.path.isfile(path):
            raise ValueError(f"Это не файл: {path}")
        
        # Чтение
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            return content
        except UnicodeDecodeError:
            raise ValueError(
                f"Файл '{path}' не является текстовым или использует неподдерживаемую кодировку"
            )
    
    def write_file(path: str, content: str) -> str:
        """
        Записывает содержимое в файл.
        
        Args:
            path: Путь к файлу
            content: Содержимое для записи
        
        Returns:
            Сообщение об успехе
        
        Raises:
            PermissionError: Если доступ запрещён
            IsADirectoryError: Если путь указывает на папку
        """
        # Жёсткая блокировка .env
        if _contains_env_filename(path):
            incidents_logger.warning(
                f"ПОПЫТКА ЗАПИСИ .env: {path} | Заблокировано"
            )
            raise PermissionError(
                f"Запись в файлы .env запрещена правилами безопасности"
            )
        
        # Проверка белого списка
        if not _is_path_allowed(path, allowed_paths):
            incidents_logger.warning(
                f"ПОПЫТКА ВЫХОДА ЗА ПРЕДЕЛЫ: write_file('{path}') | "
                f"Разрешённые пути: {allowed_paths}"
            )
            raise PermissionError(
                f"Путь '{path}' не входит в список разрешённых"
            )
        
        # Проверка, что это не папка
        if os.path.exists(path) and os.path.isdir(path):
            raise IsADirectoryError(f"Это папка, а не файл: {path}")
        
        # Создаём родительские папки если нужно
        parent_dir = os.path.dirname(path)
        if parent_dir and not os.path.exists(parent_dir):
            try:
                os.makedirs(parent_dir, exist_ok=True)
            except OSError as e:
                raise OSError(f"Не удалось создать папки для файла: {e}")
        
        # Запись
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"Файл успешно записан: {path} ({len(content)} символов)"
        except OSError as e:
            raise OSError(f"Не удалось записать файл: {e}")
    
    def list_directory(path: str) -> str:
        """
        Возвращает список файлов и папок в указанной директории.
        
        Args:
            path: Путь к папке
        
        Returns:
            Список элементов директории
        
        Raises:
            PermissionError: Если доступ запрещён
            FileNotFoundError: Если папка не найдена
        """
        # Жёсткая блокировка .env (проверяем путь)
        if _contains_env_filename(path):
            incidents_logger.warning(
                f"ПОПЫТКА ЧТЕНИЯ ПАПКИ С .env: {path} | Заблокировано"
            )
            raise PermissionError(
                f"Доступ к папкам с .env запрещён"
            )
        
        # Проверка белого списка
        if not _is_path_allowed(path, allowed_paths):
            incidents_logger.warning(
                f"ПОПЫТКА ВЫХОДА ЗА ПРЕДЕЛЫ: list_directory('{path}') | "
                f"Разрешённые пути: {allowed_paths}"
            )
            raise PermissionError(
                f"Путь '{path}' не входит в список разрешённых"
            )
        
        # Проверка существования
        if not os.path.exists(path):
            raise FileNotFoundError(f"Папка не найдена: {path}")
        
        if not os.path.isdir(path):
            raise NotADirectoryError(f"Это не папка: {path}")
        
        # Чтение содержимого
        try:
            items = os.listdir(path)
            result_lines = []
            for item in sorted(items):
                full_path = os.path.join(path, item)
                item_type = "📁" if os.path.isdir(full_path) else "📄"
                result_lines.append(f"{item_type} {item}")
            
            if not result_lines:
                return f"Папка пуста: {path}"
            
            return "\n".join(result_lines)
        except OSError as e:
            raise OSError(f"Не удалось прочитать папку: {e}")
    
    def echo(text: str) -> str:
        """
        Возвращает переданный текст (для smoke-теста function calling).
        
        Args:
            text: Текст для возврата
        
        Returns:
            Тот же текст
        """
        return f"Echo: {text}"
    
    return {
        "read_file": read_file,
        "write_file": write_file,
        "list_directory": list_directory,
        "echo": echo,
    }


# ============================================================================
# Функция создания реестра
# ============================================================================

def create_tool_registry(allowed_paths: List[str]) -> ToolRegistry:
    """
    Создаёт и заполняет реестр инструментов базовыми инструментами.
    
    Args:
        allowed_paths: Список разрешённых путей из конфига
    
    Returns:
        Заполненный ToolRegistry
    """
    registry = ToolRegistry()
    tools = create_file_tools(allowed_paths)
    
    # read_file
    registry.register(
        name="read_file",
        description="Читает содержимое текстового файла по указанному пути",
        func=tools["read_file"],
        parameters_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Полный путь к файлу"
                }
            },
            "required": ["path"]
        }
    )
    
    # write_file
    registry.register(
        name="write_file",
        description="Записывает текст в файл по указанному пути",
        func=tools["write_file"],
        parameters_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Полный путь к файлу"
                },
                "content": {
                    "type": "string",
                    "description": "Текст для записи"
                }
            },
            "required": ["path", "content"]
        }
    )
    
    # list_directory
    registry.register(
        name="list_directory",
        description="Возвращает список файлов и папок в указанной директории",
        func=tools["list_directory"],
        parameters_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Полный путь к папке"
                }
            },
            "required": ["path"]
        }
    )
    
    # echo
    registry.register(
        name="echo",
        description="Возвращает переданный текст (для тестирования)",
        func=tools["echo"],
        parameters_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Текст для возврата"
                }
            },
            "required": ["text"]
        }
    )
    
    return registry


# ============================================================================
# Точка входа для тестирования
# ============================================================================

if __name__ == "__main__":
    # Настраиваем логирование для теста
    from core.logging_setup import setup_logging
    from core.config import load_config
    
    config = load_config()
    setup_logging(config.logs_dir)
    
    print("Тест реестра инструментов")
    print()
    
    # Создаём реестр
    registry = create_tool_registry(config.security.allowed_paths)
    
    print(f"Зарегистрировано инструментов: {len(registry.list_tools())}")
    print(f"Инструменты: {', '.join(registry.list_tools())}")
    print()
    
    # Тест echo
    print("=== Тест echo ===")
    result = registry.execute("echo", {"text": "Привет, мир!"})
    print(f"Успех: {result['success']}, Результат: {result['result']}")
    print()
    
    # Тест read_file (должен работать)
    print("=== Тест read_file (разрешённый путь) ===")
    # Читаем сам config.yaml (должен быть в allowed_paths через data/)
    test_file = str(config.project_root / "config.yaml")
    result = registry.execute("read_file", {"path": test_file})
    print(f"Успех: {result['success']}")
    if result['success']:
        print(f"Первые 100 символов: {result['result'][:100]}...")
    else:
        print(f"Ошибка: {result['error']}")
    print()
    
    # Тест read_file .env (должен отклонить)
    print("=== Тест read_file .env (должен отклонить) ===")
    env_path = str(config.project_root / ".env")
    result = registry.execute("read_file", {"path": env_path})
    print(f"Успех: {result['success']}")
    print(f"Ошибка: {result['error']}")
    print()
    
    # Тест read_file вне белого списка (должен отклонить)
    print("=== Тест read_file /etc/passwd (должен отклонить) ===")
    result = registry.execute("read_file", {"path": "/etc/passwd"})
    print(f"Успех: {result['success']}")
    print(f"Ошибка: {result['error']}")
    print()
    
    # Тест list_directory
    print("=== Тест list_directory ===")
    data_dir = str(config.data_dir)
    result = registry.execute("list_directory", {"path": data_dir})
    print(f"Успех: {result['success']}")
    if result['success']:
        print(f"Содержимое:\n{result['result'][:300]}")
    else:
        print(f"Ошибка: {result['error']}")