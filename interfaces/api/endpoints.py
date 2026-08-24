from fastapi import APIRouter, HTTPException, Request, Depends
from core.config import load_config
from core.health_check import run_health_check
from core.agent_loader import load_agent, list_agents
from core.ai_client import get_client
from core.tools import create_tool_registry
from core.function_calling import (
    run_function_calling_loop,
    TOOL_LIMIT_WARNING,
    EMPTY_RESPONSE_WARNING,
)
from interfaces.api.auth import (
    check_bruteforce, record_failed_attempt, reset_attempts, 
    create_jwt_token, verify_jwt_token
)
import logging

logger = logging.getLogger("pumka.system")
router = APIRouter()

@router.post("/auth/login")
def login(data: dict, request: Request):
    """Вход по паролю из конфига."""
    client_ip = request.client.host
    check_bruteforce(client_ip)
    
    config = load_config()
    if data.get("password") == config.gui.password:
        reset_attempts(client_ip)
        return {"token": create_jwt_token()}
    else:
        record_failed_attempt(client_ip)
        raise HTTPException(status_code=401, detail="Неверный пароль")

@router.get("/health")
def health_check(_=Depends(verify_jwt_token)):
    """Возвращает отчёт о здоровье системы как есть."""
    return run_health_check().to_dict()

@router.post("/chat")
def chat(data: dict, _=Depends(verify_jwt_token)):
    """Демо-чат без памяти диалога."""
    message = data.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="Поле 'message' обязательно")

    try:
        agent = load_agent("demo")
        if not agent:
            raise HTTPException(status_code=500, detail="Демо-агент не найден или повреждён")
    except Exception as e:
        logger.error(f"Ошибка загрузки демо-агента: {e}")
        raise HTTPException(status_code=500, detail="Демо-агент не найден или повреждён")

    # Инициализация клиента и инструментов
    config = load_config()
    client = get_client(provider=config.llm.provider, ollama_url=config.llm.ollama_url)
    registry = create_tool_registry(config.security.allowed_paths)

    # Цикл function calling (5 итераций)
    try:
        final_response, iterations, tool_calls_log, hit_limit = run_function_calling_loop(
            client=client,
            agent=agent,
            prompt=message,
            system_prompt=agent.system_prompt,
            tool_registry=registry,
            temperature=0.7,
            max_tokens=2048,
            max_iterations=5
        )

        # Обработка пустого ответа
        if not final_response:
            return {
                "reply": EMPTY_RESPONSE_WARNING,
                "iterations": iterations,
                "tool_calls": [tc["name"] for tc in tool_calls_log]
            }

        # Добавляем предупреждение о лимите итераций
        if hit_limit:
            final_response = final_response + "\n\n" + TOOL_LIMIT_WARNING

        # Формируем ответ с аддитивными полями
        return {
            "reply": final_response,
            "iterations": iterations,
            "tool_calls": [tc["name"] for tc in tool_calls_log]
        }

    except Exception as e:
        logger.error(f"Ошибка в чате: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка обработки запроса: {str(e)}")

@router.get("/agents")
def get_agents_list(_=Depends(verify_jwt_token)):
    """Возвращает список агентов."""
    agents = list_agents()
    return [
        {
            "name": a.name,
            "display_name": a.display_name,
            "department": a.department,
            "role": a.role,
            "tier": a.model.tier
        }
        for a in agents
    ]

@router.get("/config")
def get_config(_=Depends(verify_jwt_token)):
    """Возвращает конфиг БЕЗ секретов."""
    config = load_config()
    return {
        "paths": {
            "vault": str(config.project_root / config.paths.data),
            "projects": str(config.project_root / config.paths.projects),
        },
        "llm": {
            "provider": config.llm.provider,
            "ollama_url": config.llm.ollama_url,
            "default_profile": config.llm.default_profile,
            "profiles": {k: v.dict() for k, v in config.llm.profiles.items()}
        },
        "security": {
            "allowed_paths": config.security.allowed_paths
        }
    }

@router.put("/config")
def update_config(data: dict, _=Depends(verify_jwt_token)):
    """ЗАГЛУШКА: логирует факт изменения, но не применяет."""
    logger.info(f"ЗАПРОС НА ИЗМЕНЕНИЕ КОНФИГА (заглушка): {data}")
    return {"status": "logged", "message": "Изменение сохранено в лог (применится на следующих этапах)"}
