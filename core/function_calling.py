"""
core/function_calling.py — парсеры вызовов инструментов.

Поддерживает два формата:
1. OpenAI format — JSON с полем tool_calls
2. Custom format — [[tool_name(param1="value", param2=123)]]

Выбирает парсер в зависимости от модели.
"""

import json
import re
import logging
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger("pumka.system")


# ============================================================================
# Модели, поддерживающие OpenAI-формат function calling
# ============================================================================

OPENAI_FORMAT_MODELS = {
    "Qwen2.5-3B-Instruct-AWQ",
    "Qwen3-4B-Instruct-2507",
    "Qwen3.5-9B",
    "Qwen2.5-Coder-3B-Instruct-AWQ",
    "Qwen2.5-Coder-1.5B-Instruct",
}


def choose_parser(model_name: str) -> str:
    """
    Определяет, какой парсер использовать для модели.

    Args:
        model_name: Имя модели (HF или Ollama формат)

    Returns:
        "openai" или "custom"
    """
    # Проверяем точное совпадение
    if model_name in OPENAI_FORMAT_MODELS:
        return "openai"

    # Проверяем вхождения (на случай если имя с префиксом)
    for supported_model in OPENAI_FORMAT_MODELS:
        if supported_model in model_name:
            return "openai"

    return "custom"


# ============================================================================
# Парсер OpenAI-формата
# ============================================================================


def parse_openai_format(response_text: str) -> List[Dict[str, Any]]:
    """
    Извлекает вызовы инструментов из ответа в OpenAI-формате.

    Формат:
    {
        "tool_calls": [
            {
                "name": "read_file",
                "arguments": {"path": "/home/test.txt"}
            }
        ]
    }

    Args:
        response_text: Текстовый ответ от LLM

    Returns:
        Список вызовов инструментов:
        [{"name": "read_file", "params": {"path": "/home/test.txt"}}, ...]
    """
    tool_calls = []

    # Пытаемся распарсить как JSON
    try:
        # Ищем JSON в ответе (модель может добавить текст до/после)
        json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())

            # Проверяем наличие tool_calls
            if "tool_calls" in data:
                for call in data["tool_calls"]:
                    name = call.get("function", {}).get("name") or call.get("name")
                    arguments = call.get("function", {}).get("arguments") or call.get(
                        "arguments", {}
                    )

                    # arguments может быть строкой JSON или уже словарём
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            logger.warning(
                                f"Не удалось распарсить аргументы: {arguments}"
                            )
                            continue

                    if name:
                        tool_calls.append({"name": name, "params": arguments})

    except (json.JSONDecodeError, AttributeError) as e:
        logger.debug(f"Не удалось распарсить как OpenAI-формат: {e}")

    return tool_calls


# ============================================================================
# Парсер кастомного формата
# ============================================================================


def parse_custom_format(response_text: str) -> List[Dict[str, Any]]:
    """
    Извлекает вызовы инструментов из ответа в кастомном формате.

    Формат:
    [[tool_name(param1="value", param2=123, param3=true)]]

    Args:
        response_text: Текстовый ответ от LLM

    Returns:
        Список вызовов инструментов
    """
    tool_calls = []

    # Regex для поиска вызовов: [[name(param1="val", param2=123)]]
    pattern = r"\[\[(\w+)\(([^)]*)\)\]\]"
    matches = re.finditer(pattern, response_text)

    for match in matches:
        tool_name = match.group(1)
        args_str = match.group(2)

        # Парсим аргументы
        params = {}

        # Regex для аргументов: key=value (значение может быть в кавычках или числом)
        arg_pattern = r'(\w+)\s*=\s*("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|[^,\s\]]+)'
        arg_matches = re.finditer(arg_pattern, args_str)

        for arg_match in arg_matches:
            key = arg_match.group(1)
            value = arg_match.group(2)

            # Убираем кавычки если есть
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
                # Обрабатываем escape-последовательности
                value = (
                    value.replace('\\"', '"').replace("\\'", "'").replace("\\n", "\n")
                )
            else:
                # Пробуем преобразовать в число или булево
                if value.lower() == "true":
                    value = True
                elif value.lower() == "false":
                    value = False
                elif value.lower() == "null" or value.lower() == "none":
                    value = None
                else:
                    try:
                        if "." in value:
                            value = float(value)
                        else:
                            value = int(value)
                    except ValueError:
                        pass  # Оставляем как строку

            params[key] = value

        tool_calls.append({"name": tool_name, "params": params})

    return tool_calls


# ============================================================================
# Универсальный парсер
# ============================================================================


def parse_tool_calls(response_text: str, model_name: str) -> List[Dict[str, Any]]:
    """
    Извлекает вызовы инструментов из ответа LLM.

    Сначала пробует формат, подходящий для модели, затем fallback.

    Args:
        response_text: Текстовый ответ от LLM
        model_name: Имя модели

    Returns:
        Список вызовов инструментов
    """
    parser_type = choose_parser(model_name)

    if parser_type == "openai":
        result = parse_openai_format(response_text)
        if result:
            logger.debug(f"Парсер OpenAI нашёл {len(result)} вызовов")
            return result

        # Fallback на custom
        result = parse_custom_format(response_text)
        if result:
            logger.debug(f"Fallback на custom парсер: {len(result)} вызовов")
            return result

    else:  # custom
        result = parse_custom_format(response_text)
        if result:
            logger.debug(f"Парсер custom нашёл {len(result)} вызовов")
            return result

        # Fallback на OpenAI
        result = parse_openai_format(response_text)
        if result:
            logger.debug(f"Fallback на OpenAI парсер: {len(result)} вызовов")
            return result

    return []


# ============================================================================
# Выполнение вызовов
# ============================================================================


def execute_tool_calls(
    tool_calls: List[Dict[str, Any]], tool_registry
) -> List[Dict[str, Any]]:
    """
    Выполняет список вызовов инструментов.

    Args:
        tool_calls: Список вызовов [{"name": "...", "params": {...}}, ...]
        tool_registry: ToolRegistry для выполнения

    Returns:
        Список результатов [{"name": "...", "success": bool, "result": Any, "error": Optional[str]}, ...]
    """
    results = []

    for call in tool_calls:
        name = call.get("name", "")
        params = call.get("params", {})

        logger.info(f"Выполнение инструмента: {name}({params})")

        result = tool_registry.execute(name, params)

        results.append(
            {
                "name": name,
                "success": result["success"],
                "result": result["result"],
                "error": result["error"],
            }
        )

    return results


# ============================================================================
# Полный цикл обработки ответа
# ============================================================================


def process_response(
    response: str, model_name: str, tool_registry
) -> Tuple[str, List[Dict[str, Any]], bool]:
    """
    Полный цикл обработки ответа LLM.

    Args:
        response: Текстовый ответ от LLM
        model_name: Имя модели
        tool_registry: ToolRegistry для выполнения инструментов

    Returns:
        (cleaned_response, tool_results, has_tool_calls)
        - cleaned_response: Текст ответа без вызовов инструментов
        - tool_results: Список результатов выполнения инструментов
        - has_tool_calls: Были ли вызовы инструментов
    """
    # Извлекаем вызовы инструментов
    tool_calls = parse_tool_calls(response, model_name)

    if not tool_calls:
        return response, [], False

    # Выполняем инструменты
    tool_results = execute_tool_calls(tool_calls, tool_registry)

    # Убираем вызовы из текста ответа
    cleaned_response = response

    # Убираем OpenAI-формат
    json_match = re.search(r'\{.*"tool_calls".*\}', response, re.DOTALL)
    if json_match:
        cleaned_response = response[: json_match.start()] + response[json_match.end() :]

    # Убираем custom-формат
    cleaned_response = re.sub(r"\[\[\w+\([^)]*\)\]\]", "", cleaned_response)

    cleaned_response = cleaned_response.strip()

    return cleaned_response, tool_results, True


# ============================================================================
# Константы для предупреждений
# ============================================================================
TOOL_LIMIT_WARNING = (
    "⚠️ Я попытался использовать слишком много инструментов и остановился."
)
EMPTY_RESPONSE_WARNING = "⚠️ Не удалось получить ответ. Попробуйте ещё раз."


# ============================================================================
# Общая функция function calling (5 итераций)
# ============================================================================
def run_function_calling_loop(
    client,
    agent,
    prompt: str,
    system_prompt: str,
    tool_registry,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    max_iterations: int = 5,
) -> tuple:
    """
    Общий цикл function calling для Telegram и API.

    Args:
        client: LLM-клиент (OllamaClient и т.д.)
        agent: Объект Agent с конфигурацией
        prompt: Исходный запрос пользователя
        system_prompt: Системный промпт
        tool_registry: ToolRegistry для выполнения инструментов
        temperature: Температура генерации
        max_tokens: Максимальное количество токенов
        max_iterations: Лимит итераций (по умолчанию 5)

    Returns:
        (final_text, iterations, tool_calls_log, hit_limit)
        - final_text: Финальный ответ (пустая строка при ошибке)
        - iterations: Количество выполненных итераций
        - tool_calls_log: Список словарей {name, arguments, result} с уникальными именами
        - hit_limit: True если достигнут лимит итераций
    """
    import time

    tools_for_llm = None
    if agent.tools:
        tools_for_llm = tool_registry.get_openai_tools_format()

    current_prompt = prompt
    iteration = 0
    final_response = ""
    cleaned_response = ""
    hit_limit = False
    tool_calls_log = []
    seen_tool_names = set()

    while iteration < max_iterations:
        iteration += 1
        logger.info(f"Итерация {iteration}: отправка запроса в LLM")

        response = ""
        max_retries = 2

        for attempt in range(max_retries):
            try:
                response = client.generate(
                    prompt=current_prompt,
                    system_prompt=system_prompt,
                    tools=tools_for_llm,
                    model=agent.model.name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as e:
                logger.error(f"Ошибка при запросе к LLM (попытка {attempt + 1}): {e}")
                if attempt == max_retries - 1:
                    raise  # Пробрасываем исключение обёртке
                time.sleep(1)
                continue

            if response and response.strip():
                break

            logger.warning(
                f"LLM вернул пустой ответ (попытка {attempt + 1}/{max_retries}). "
                f"Повтор через 1 секунду..."
            )
            if attempt < max_retries - 1:
                time.sleep(1)
                continue

        if not response or not response.strip():
            logger.error("LLM вернул пустой ответ после всех попыток")
            return ("", iteration, tool_calls_log, False)

        cleaned_response, tool_results, has_tool_calls = process_response(
            response, agent.model.name, tool_registry
        )

        if not has_tool_calls:
            final_response = cleaned_response
            break

        logger.info(f"Найдено {len(tool_results)} вызовов инструментов")

        # Собираем логи вызовов инструментов (уникальные имена)
        for result in tool_results:
            if result["name"] not in seen_tool_names:
                tool_calls_log.append(
                    {
                        "name": result["name"],
                        "arguments": result.get("params", {}),
                        "result": result.get("result")
                        if result["success"]
                        else result.get("error"),
                    }
                )
                seen_tool_names.add(result["name"])

        # Формируем текст результатов для следующей итерации
        tool_results_text = []
        for result in tool_results:
            if result["success"]:
                tool_results_text.append(
                    f"Результат вызова {result['name']}: {result['result']}"
                )
            else:
                tool_results_text.append(
                    f"Ошибка вызова {result['name']}: {result['error']}"
                )

        current_prompt = (
            f"Предыдущий ответ: {cleaned_response}\n\n"
            f"Результаты вызовов инструментов:\n"
            + "\n".join(tool_results_text)
            + "\n\nПродолжай выполнение задачи."
        )
    else:
        logger.warning(f"Превышен лимит итераций function calling: {max_iterations}")
        hit_limit = True
        final_response = cleaned_response

    final_response = (final_response or "").strip()

    return (final_response, iteration, tool_calls_log, hit_limit)


# ============================================================================
# Точка входа для тестирования
# ============================================================================

if __name__ == "__main__":
    from core.logging_setup import setup_logging
    from core.config import load_config
    from core.tools import create_tool_registry

    config = load_config()
    setup_logging(config.logs_dir)

    registry = create_tool_registry(config.security.allowed_paths)

    print("Тест парсеров function calling")
    print()

    # Тест OpenAI-формата
    print("=== Тест OpenAI-формата ===")
    openai_response = """
    Я прочитаю файл для вас.
    {
        "tool_calls": [
            {
                "name": "read_file",
                "arguments": {"path": "/home/pumka/Pumka/config.yaml"}
            }
        ]
    }
    """
    calls = parse_openai_format(openai_response)
    print(f"Найдено вызовов: {len(calls)}")
    for call in calls:
        print(f"  {call['name']}({call['params']})")
    print()

    # Тест custom-формата
    print("=== Тест custom-формата ===")
    custom_response = """
    Сейчас я прочитаю файл [[read_file(path="/home/pumka/Pumka/config.yaml")]]
    и также протестирую echo [[echo(text="Тест работает")]]
    """
    calls = parse_custom_format(custom_response)
    print(f"Найдено вызовов: {len(calls)}")
    for call in calls:
        print(f"  {call['name']}({call['params']})")
    print()

    # Тест process_response
    print("=== Тест process_response ===")
    response = 'Я проверю содержимое [[echo(text="Hello World")]]'
    cleaned, results, has_calls = process_response(response, "test-model", registry)
    print(f"Очищенный ответ: '{cleaned}'")
    print(f"Были вызовы: {has_calls}")
    for r in results:
        print(f"  {r['name']}: success={r['success']}, result={r['result']}")
