"""
scripts/test_bot.py — smoke-тест для Telegram-бота.
Проверяет импорты, загрузку конфига, создание клавиатур без сети и токена.
"""

import sys
from pathlib import Path

# Добавляем корень проекта в sys.path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def test_imports():
    """Проверяет, что все модули импортируются."""
    print("Тест 1: Импорт модулей...")
    try:
        import interfaces.telegram
        import interfaces.telegram.keyboards
        import interfaces.telegram.handlers
        import interfaces.telegram.bot
        print("  ✅ Все модули импортированы успешно")
        return True
    except ImportError as e:
        print(f"  ❌ Ошибка импорта: {e}")
        return False


def test_config_loading():
    """Проверяет загрузку конфигурации."""
    print("\nТест 2: Загрузка конфигурации...")
    try:
        from core.config import load_config
        config = load_config()
        print(f"  ✅ Конфигурация загружена: {config}")
        print(f"     Telegram token: {'задан' if config.telegram.token else 'НЕ задан'}")
        print(f"     Allowed user ID: {config.telegram.allowed_user_id}")
        print(f"     Прокси: {config.telegram.proxy if config.telegram.proxy else 'не задан'}")
        print(f"     Автоподбор прокси: {'включён' if config.telegram.proxy_auto else 'выключен'}")
        return True
    except Exception as e:
        print(f"  ❌ Ошибка загрузки конфигурации: {e}")
        return False


def test_keyboard_creation():
    """Проверяет создание клавиатуры."""
    print("\nТест 3: Создание клавиатуры...")
    try:
        from interfaces.telegram.keyboards import main_menu_keyboard
        from aiogram.types import ReplyKeyboardMarkup
        
        keyboard = main_menu_keyboard()
        
        if not isinstance(keyboard, ReplyKeyboardMarkup):
            print(f"  ❌ Клавиатура не является ReplyKeyboardMarkup")
            return False
        
        # Проверяем количество кнопок
        total_buttons = sum(len(row) for row in keyboard.keyboard)
        if total_buttons != 4:
            print(f"  ❌ Ожидалось 4 кнопки, получено {total_buttons}")
            return False
        
        print(f"  ✅ Reply-клавиатура создана с {total_buttons} кнопками")
        return True
    except Exception as e:
        print(f"  ❌ Ошибка создания клавиатуры: {e}")
        return False


def test_handlers_registration():
    """Проверяет регистрацию обработчиков."""
    print("\nТест 4: Регистрация обработчиков...")
    try:
        from aiogram import Dispatcher
        from interfaces.telegram.handlers import router
        from core.config import load_config
        from core.tools import create_tool_registry
        
        config = load_config()
        tool_registry = create_tool_registry(config.security.allowed_paths)
        
        dp = Dispatcher()
        dp.include_router(router)
        
        # Передаём данные через workflow data Dispatcher'а
        dp["config"] = config
        dp["tool_registry"] = tool_registry
        
        print(f"  ✅ Обработчики зарегистрированы успешно")
        return True
    except Exception as e:
        print(f"  ❌ Ошибка регистрации обработчиков: {e}")
        return False


def test_proxy_config():
    """Проверяет чтение прокси из конфига."""
    print("\nТест 5: Чтение прокси из конфигурации...")
    try:
        from core.config import load_config
        config = load_config()
        
        if config.telegram.proxy:
            print(f"  ✅ Прокси задан: {config.telegram.proxy}")
            # Проверяем формат
            if config.telegram.proxy.startswith(("http://", "https://", "socks5://", "socks4://")):
                print(f"  ✅ Формат прокси корректный")
            else:
                print(f"  ⚠️ Неизвестный формат прокси (ожидается http://, https://, socks5://, socks4://)")
        else:
            print(f"  ℹ️ Прокси не задан (бот будет работать без прокси)")
        
        # Проверяем автоподбор
        if config.telegram.proxy_auto:
            print(f"  ✅ Автоподбор прокси включён")
            print(f"     API URL: {config.telegram.proxy_api_url}")
        else:
            print(f"  ℹ️ Автоподбор прокси выключен")
        
        return True
    except Exception as e:
        print(f"  ❌ Ошибка проверки прокси: {e}")
        return False


def test_proxy_functions():
    """Проверяет функции нормализации прокси."""
    print("\nТест 6: Функции нормализации прокси...")
    try:
        from interfaces.telegram.bot import normalize_proxy
        
        # Тест нормализации
        test_cases = [
            ("1.2.3.4:1080", "socks5://1.2.3.4:1080"),
            ("socks5://1.2.3.4:1080", "socks5://1.2.3.4:1080"),
            ("http://1.2.3.4:8080", "http://1.2.3.4:8080"),
            ("", ""),
        ]
        
        for input_val, expected in test_cases:
            result = normalize_proxy(input_val)
            if result != expected:
                print(f"  ❌ normalize_proxy('{input_val}') вернул '{result}', ожидалось '{expected}'")
                return False
        
        print(f"  ✅ Функция normalize_proxy работает корректно")
        return True
    except Exception as e:
        print(f"  ❌ Ошибка тестирования функций прокси: {e}")
        return False


def test_proxy_advanced_config():
    """Проверяет новые настройки прокси (DoH и выбор самого быстрого)."""
    print("\nТест 7: Новые настройки прокси...")
    try:
        from core.config import load_config
        config = load_config()
        print(f"  DoH для списка прокси: {'включён' if config.telegram.proxy_use_doh else 'выключен'}")
        print(f"  DoH URL: {config.telegram.proxy_doh_url}")
        print(f"  Выбор самого быстрого: {'включён' if config.telegram.proxy_select_best else 'выключен'}")
        return True
    except Exception as e:
        print(f"  ❌ Ошибка: {e}")
        return False


def main():
    """Запускает все тесты."""
    print("=" * 60)
    print("Smoke-тест Telegram-бота Pumka (Этап 2)")
    print("=" * 60)
    
    tests = [
        test_imports,
        test_config_loading,
        test_keyboard_creation,
        test_handlers_registration,
        test_proxy_config,
        test_proxy_functions,
        test_proxy_advanced_config,
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"  ❌ Непредвиденная ошибка: {e}")
            results.append(False)
    
    print("\n" + "=" * 60)
    print("Результаты тестов:")
    print("=" * 60)
    
    passed = sum(results)
    total = len(results)
    
    print(f"Пройдено: {passed}/{total}")
    
    if passed == total:
        print("\n✅ Все тесты пройдены успешно!")
        print("\nДля ручного теста в Telegram:")
        print("  1. Запустите бота: python -m interfaces.telegram.bot")
        print("  2. Отправьте /start в Telegram")
        print("  3. Проверьте кнопки и диалог")
        return 0
    else:
        print("\n❌ Некоторые тесты не пройдены")
        return 1


if __name__ == "__main__":
    sys.exit(main())