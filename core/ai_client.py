"""
core/ai_client.py — абстракция над LLM-провайдерами.

Предоставляет унифицированный интерфейс для работы с разными LLM:
- Ollama (локальный, рабочая реализация)
- OpenRouter (заглушка, будет реализовано позже)
- RouterAI (заглушка, будет реализовано позже)
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from core.llm_registry import resolve_ollama_name

logger = logging.getLogger("pumka.system")


# ============================================================================
# Базовый абстрактный класс
# ============================================================================

class BaseAIClient(ABC):
    """Абстрактный базовый класс для всех LLM-клиентов."""
    
    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs
    ) -> str:
        """
        Генерирует ответ от LLM.
        
        Args:
            prompt: Текст запроса пользователя
            system_prompt: Системный промпт (инструкции для модели)
            tools: Список инструментов (для function calling)
            **kwargs: Дополнительные параметры (temperature, max_tokens и т.д.)
        
        Returns:
            Текстовый ответ модели
        """
        pass


# ============================================================================
# Ollama клиент (рабочая реализация)
# ============================================================================

class OllamaClient(BaseAIClient):
    """Клиент для работы с локальной Ollama."""
    
    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url.rstrip('/')
        logger.info(f"OllamaClient инициализирован: {self.base_url}")
    
    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stream: bool = False,
        **kwargs
    ) -> str:
        """
        Отправляет запрос к Ollama API.
        
        Использует /api/chat эндпоинт для совместимости с OpenAI-форматом.
        """
        
        if not model:
            raise ValueError("Не указано имя модели для Ollama")
        
        # Преобразуем HF-имя в Ollama-имя
        ollama_model = resolve_ollama_name(model)
        
        # Формируем сообщения
        messages = []
        
        if system_prompt:
            messages.append({
                "role": "system",
                "content": system_prompt
            })
        
        messages.append({
            "role": "user",
            "content": prompt
        })
        
        # Формируем запрос
        request_data = {
            "model": ollama_model,
            "messages": messages,
            "stream": stream,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            }
        }
        
        # Если переданы инструменты — добавляем их
        if tools:
            request_data["tools"] = tools
        
        # Отправляем запрос
        url = f"{self.base_url}/api/chat"
        
        try:
            logger.info(f"Отправка запроса к Ollama: model={ollama_model}, prompt_length={len(prompt)}")
            
            req = Request(
                url,
                data=json.dumps(request_data).encode('utf-8'),
                headers={
                    'Content-Type': 'application/json'
                },
                method='POST'
            )
            
            with urlopen(req, timeout=120) as response:
                response_data = json.loads(response.read().decode('utf-8'))
            
            # Извлекаем ответ
            if "message" in response_data and "content" in response_data["message"]:
                result = response_data["message"]["content"]
                logger.info(f"Получен ответ от Ollama: length={len(result)}")
                return result
            else:
                logger.error(f"Неожиданный формат ответа от Ollama: {response_data}")
                return f"[ОШИБКА] Неожиданный формат ответа от Ollama"
        
        except HTTPError as e:
            error_body = e.read().decode('utf-8', errors='replace')
            logger.error(f"HTTP ошибка при запросе к Ollama: {e.code} - {error_body}")
            return f"[ОШИБКА] Ollama вернул ошибку {e.code}: {error_body[:200]}"
        
        except URLError as e:
            logger.error(f"Ошибка сети при запросе к Ollama: {e}")
            return f"[ОШИБКА] Не удалось подключиться к Ollama по адресу {url}. Проверьте, что Ollama запущен."
        
        except Exception as e:
            logger.error(f"Неожиданная ошибка при запросе к Ollama: {e}")
            return f"[ОШИБКА] Неожиданная ошибка при работе с Ollama: {e}"


# ============================================================================
# Заглушки для облачных провайдеров
# ============================================================================

class OpenRouterClient(BaseAIClient):
    """Клиент для OpenRouter (заглушка)."""
    
    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs
    ) -> str:
        raise NotImplementedError(
            "OpenRouter будет реализован позже. "
            "Сейчас используйте провайдер 'ollama'."
        )


class RouterAIClient(BaseAIClient):
    """Клиент для RouterAI (заглушка)."""
    
    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs
    ) -> str:
        raise NotImplementedError(
            "RouterAI будет реализован позже. "
            "Сейчас используйте провайдер 'ollama'."
        )


# ============================================================================
# Фабрика клиентов
# ============================================================================

def get_client(provider: str, **kwargs) -> BaseAIClient:
    """
    Возвращает клиент для указанного провайдера.
    
    Args:
        provider: Имя провайдера ("ollama", "openrouter", "routerai")
        **kwargs: Дополнительные параметры для клиента (например, ollama_url)
    
    Returns:
        Экземпляр соответствующего клиента
    
    Raises:
        ValueError: Если провайдер неизвестен
    """
    
    provider = provider.lower()
    
    if provider == "ollama":
        base_url = kwargs.get("ollama_url", "http://localhost:11434")
        return OllamaClient(base_url=base_url)
    
    elif provider == "openrouter":
        return OpenRouterClient()
    
    elif provider == "routerai":
        return RouterAIClient()
    
    else:
        raise ValueError(
            f"Неизвестный провайдер: '{provider}'. "
            f"Доступные провайдеры: ollama, openrouter, routerai"
        )


# ============================================================================
# Точка входа для тестирования
# ============================================================================

if __name__ == "__main__":
    print("Тест клиентов LLM")
    print()
    
    # Тест Ollama
    print("=== Ollama ===")
    try:
        ollama = get_client("ollama", ollama_url="http://192.168.0.63:11434")
        print(f"✅ OllamaClient создан успешно")
        
        # Попытка сгенерировать простой ответ
        print("Попытка генерации ответа...")
        result = ollama.generate(
            prompt="Скажи 'Привет' одним словом",
            model="Qwen2.5-3B-Instruct-AWQ",
            max_tokens=50
        )
        print(f"Ответ: {result[:100]}")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    
    print()
    
    # Тест заглушек
    print("=== OpenRouter (заглушка) ===")
    try:
        openrouter = get_client("openrouter")
        openrouter.generate("test")
    except NotImplementedError as e:
        print(f"✅ Заглушка работает: {e}")
    
    print()
    print("=== RouterAI (заглушка) ===")
    try:
        routerai = get_client("routerai")
        routerai.generate("test")
    except NotImplementedError as e:
        print(f"✅ Заглушка работает: {e}")