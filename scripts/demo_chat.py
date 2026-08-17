"""
scripts/demo_chat.py — демонстрационный чат с демо-агентом.

Проверяет работу ядра системы:
- Загрузка конфигурации
- Инициализация логирования
- Создание реестра инструментов
- Загрузка агента
- Общение с пользователем через Ollama
- Вызов инструментов через function calling

Запуск: python scripts/demo_chat.py
"""

import sys
from pathlib import Path

# Добавляем корень проекта в путь для импортов
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.config import load_config
from core.logging_setup import setup_logging, get_logger
from core.tools import create_tool_registry
from core.agent_loader import get_loader
from core.ai_client import get_client
from core.function_calling import process_response, parse_tool_calls


def print_banner():
    """Выводит приветственный баннер."""
    print()
    print("=" * 60)
    print("🤖 Pumka — Демонстрационный чат (Этап 1)")
    print("=" * 60)
    print("Это smoke-тест ядра системы.")
    print("Демо-агент может отвечать на вопросы и использовать инструменты:")
    print("  • read_file — чтение файлов")
    print("  • echo — возврат текста (для теста)")
    print()
    print("Команды:")
    print("  • /help — показать эту справку")
    print("  • /info — информация об агенте")
    print("  • /quit или /exit — выйти")
    print("=" * 60)
    print()


def print_help():
    """Выводит справку."""
    print()
    print("📖 Справка:")
    print("  Просто пишите сообщения агенту.")
    print("  Примеры запросов:")
    print("    • 'Прочитай файл config.yaml'")
    print("    • 'Что находится в папке data?'")
    print("    • 'Проверь работу echo с текстом привет'")
    print("  Команды: /help, /info, /quit, /exit")
    print()


def print_agent_info(agent):
    """Выводит информацию об агенте."""
    print()
    print(f"👤 Агент: {agent.display_name}")
    print(f"   Имя: {agent.name}")
    print(f"   Отдел: {agent.department}")
    print(f"   Роль: {agent.role}")
    print(f"   Модель: {agent.model.name} (tier: {agent.model.tier})")
    print(f"   Провайдер: {agent.model.provider}")
    print(f"   Инструменты: {', '.join(agent.tools)}")
    print(f"   Приоритет: {agent.priority}")
    print()


def main():
    """Главная функция чата."""
    
    # 1. Загружаем конфигурацию
    print("Загрузка конфигурации...")
    config = load_config()
    
    # 2. Настраиваем логирование
    setup_logging(config.logs_dir)
    logger = get_logger("pumka.system")
    logger.info("=== Запуск demo_chat ===")
    
    # 3. Создаём реестр инструментов
    tool_registry = create_tool_registry(config.security.allowed_paths)
    logger.info(f"Создан реестр инструментов: {tool_registry.list_tools()}")
    
    # 4. Загружаем демо-агента
    loader = get_loader(config.project_root)
    agent = loader.load_agent("demo")
    
    if not agent:
        print("❌ ОШИБКА: Демо-агент не найден!")
        print("   Проверьте файл agents/builtin/demo.yaml")
        sys.exit(1)
    
    # 5. Создаём AI-клиент
    ai_client = get_client(
        provider=agent.model.provider,
        ollama_url=config.llm.ollama_url
    )
    
    # 6. Запускаем чат
    print_banner()
    print_agent_info(agent)
    
    print("Агент готов к работе. Начните диалог.")
    print()
    
    while True:
        try:
            # Получаем сообщение от пользователя
            user_input = input("Вы: ").strip()
            
            if not user_input:
                continue
            
            # Обработка команд
            if user_input.lower() in ("/quit", "/exit", "/q"):
                print()
                print("До свидания! 👋")
                logger.info("=== Завершение demo_chat ===")
                break
            
            if user_input.lower() == "/help":
                print_help()
                continue
            
            if user_input.lower() == "/info":
                print_agent_info(agent)
                continue
            
            # Отправляем запрос к LLM
            print()
            print("Агент думает...")
            
            response = ai_client.generate(
                prompt=user_input,
                system_prompt=agent.system_prompt,
                model=agent.model.name,
                temperature=0.7,
                max_tokens=1024,
            )
            
            # Обрабатываем ответ (извлекаем tool calls)
            cleaned_response, tool_results, has_tool_calls = process_response(
                response=response,
                model_name=agent.model.name,
                tool_registry=tool_registry
            )
            
            # Если были вызовы инструментов — показываем результаты
            if has_tool_calls and tool_results:
                print()
                print("🔧 Вызваны инструменты:")
                for result in tool_results:
                    status_icon = "✅" if result["success"] else "❌"
                    print(f"   {status_icon} {result['name']}")
                    
                    if result["success"]:
                        result_str = str(result["result"])
                        if len(result_str) > 200:
                            result_str = result_str[:200] + "..."
                        print(f"      Результат: {result_str}")
                    else:
                        print(f"      Ошибка: {result['error']}")
                
                print()
                print("Агент обрабатывает результаты...")
                
                # Повторный запрос к LLM с результатами инструментов
                # Формируем контекст с результатами
                tool_results_text = "\n".join([
                    f"Инструмент {r['name']}: " + 
                    (f"успешно выполнился. Результат:\n{r['result']}" if r['success'] 
                     else f"завершился с ошибкой: {r['error']}")
                    for r in tool_results
                ])
                
                follow_up_prompt = (
                    f"Исходный запрос пользователя: {user_input}\n\n"
                    f"Результаты выполнения инструментов:\n{tool_results_text}\n\n"
                    f"Теперь дай финальный ответ пользователю на основе этих результатов."
                )
                
                final_response = ai_client.generate(
                    prompt=follow_up_prompt,
                    system_prompt=agent.system_prompt,
                    model=agent.model.name,
                    temperature=0.7,
                    max_tokens=1024,
                )
                
                print()
                print(f"Агент: {final_response}")
                logger.info(f"Финальный ответ: {final_response[:100]}...")
            
            else:
                # Обычный ответ без вызовов инструментов
                print()
                print(f"Агент: {cleaned_response}")
                logger.info(f"Ответ: {cleaned_response[:100]}...")
            
            print()
        
        except KeyboardInterrupt:
            print()
            print()
            print("Прервано пользователем. До свидания! 👋")
            logger.info("=== Прерывание demo_chat ===")
            break
        
        except Exception as e:
            print()
            print(f"❌ Ошибка: {e}")
            logger.error(f"Ошибка в чате: {e}", exc_info=True)
            print("Попробуйте ещё раз.")
            print()


if __name__ == "__main__":
    main()