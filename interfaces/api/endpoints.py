from pathlib import Path
import uuid
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


# Ограничения для контекста мультичата (согласовано с архитектором)
MAX_MSG_CHARS = 2000      # максимум символов на одно сообщение
MAX_PROMPT_CHARS = 8000   # максимум символов на общий промпт


def _build_prompt_with_history(history: list, current_message: str) -> str:
    """Формирует промпт с историей сообщений для мультичата."""
    current_message = current_message.strip()[:MAX_MSG_CHARS]

    if not history:
        return current_message

    lines = ["Предыдущие сообщения:"]
    for msg in history:
        role = msg["role"]
        msg_content = msg["content"].strip()[:MAX_MSG_CHARS]
        lines.append(f"{role}: {msg_content}")

    lines.append("")
    lines.append(f"Новый запрос: {current_message}")

    prompt = "\n".join(lines)

    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS]

    return prompt


@router.post("/chat")
def chat(data: dict, _=Depends(verify_jwt_token)):
    """Чат с опциональной историей (мультичат)."""
    message = data.get("message", "")
    chat_id = data.get("chat_id", "")

    if not message:
        raise HTTPException(status_code=400, detail="Поле 'message' обязательно")

    # Пустой или отсутствующий chat_id = stateless режим
    chat_id = (chat_id or "").strip()

    try:
        agent = load_agent("demo")
        if not agent:
            raise HTTPException(status_code=500, detail="Демо-агент не найден или повреждён")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка загрузки демо-агента: {e}")
        raise HTTPException(status_code=500, detail="Демо-агент не найден или повреждён")

    config = load_config()
    client = get_client(provider=config.llm.provider, ollama_url=config.llm.ollama_url)
    registry = create_tool_registry(config.security.allowed_paths)

    # Мультичат: загрузка истории и формирование контекста
    storage = None
    if chat_id:
        storage = _get_chat_storage()
        chat_data = storage.get_chat(chat_id)
        if chat_data is None:
            raise HTTPException(status_code=404, detail="Чат не найден")

        history_size = config.chat.history_size
        history = storage.get_history(chat_id, limit=history_size - 1)
        prompt = _build_prompt_with_history(history, message)
    else:
        prompt = message

    # Вызов LLM
    try:
        final_response, iterations, tool_calls_log, hit_limit = run_function_calling_loop(
            client=client,
            agent=agent,
            prompt=prompt,
            system_prompt=agent.system_prompt,
            tool_registry=registry,
            temperature=0.7,
            max_tokens=2048,
            max_iterations=5
        )

        if not final_response:
            final_response = EMPTY_RESPONSE_WARNING

        if hit_limit:
            final_response = final_response + "\n" + TOOL_LIMIT_WARNING

        # Мультичат: сохранение сообщений
        if chat_id and storage:
            try:
                ts_user = storage.add_message(chat_id, "user", message.strip())
                ts_assistant = storage.add_message(chat_id, "assistant", final_response.strip())
                
                # Upsert в ChromaDB коллекцию chat_messages (закрытие отложенного Этапа 5)
                try:
                    from services.chroma.client import ChromaClient
                    from core.config import load_config as _load_config_for_chroma
                    _cfg = _load_config_for_chroma()
                    _chroma_dir = _cfg.project_root / "data" / "chroma"
                    _chroma = ChromaClient(_chroma_dir)
                    if _chroma.available and _chroma.chat_messages is not None:
                        _chroma.chat_messages.upsert(
                            ids=[str(uuid.uuid4()), str(uuid.uuid4())],
                            documents=[message.strip(), final_response.strip()],
                            metadatas=[
                                {"chat_id": chat_id, "role": "user", "timestamp": ts_user},
                                {"chat_id": chat_id, "role": "assistant", "timestamp": ts_assistant},
                            ]
                        )
                except Exception as chroma_e:
                    logger.warning(f"Не удалось сохранить сообщения в ChromaDB (chat_id={chat_id}): {chroma_e}")
                    
            except Exception as e:
                logger.error(f"Ошибка сохранения сообщений в чат {chat_id}: {e}")

        # Формируем ответ
        response = {
            "reply": final_response,
            "iterations": iterations,
            "tool_calls": [tc["name"] for tc in tool_calls_log]
        }
        if chat_id:
            response["chat_id"] = chat_id

        return response

    except HTTPException:
        raise
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
            "vault": config.paths.vault_path,
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

@router.post("/index-vault")
async def index_vault_endpoint(_=Depends(verify_jwt_token)):
    """
    Запускает индексацию Obsidian Vault в ChromaDB.
    Выполняется в фоне, не блокирует запрос.
    Возвращает результат индексации.
    """
    from services.chroma.autoindex import run_index_vault
    from services.chroma.client import ChromaClient
    
    config = load_config()
    
    # Создаём ChromaClient
    chroma_dir = config.project_root / "data" / "chroma"
    chroma_client = ChromaClient(chroma_dir)
    
    # Запускаем индексацию в фоне
    result = await run_index_vault(
        vault_root=Path(config.paths.vault_path),
        chroma_client=chroma_client,
        max_file_size_mb=config.security.max_file_size_mb,
        background=False  # Синхронно, чтобы сразу вернуть результат
    )
    
    if result["status"] == "already_running":
        raise HTTPException(
            status_code=409,
            detail="Индексация уже выполняется. Подождите завершения."
        )
    
    return result

# ============================================================================
# Эндпоинты мультичата (Этап 5)
# ============================================================================

from services.chat_history.storage import ChatStorage

# Глобальный экземпляр хранилища чатов (инициализируется при первом запросе)
_chat_storage: ChatStorage = None


def _get_chat_storage() -> ChatStorage:
    """Возвращает экземпляр ChatStorage, создавая его при первом вызове."""
    global _chat_storage
    if _chat_storage is None:
        config = load_config()
        db_dir = config.project_root / "data" / "chats"
        _chat_storage = ChatStorage(db_dir)
    return _chat_storage


@router.get("/chats")
async def list_chats(_=Depends(verify_jwt_token)):
    """Возвращает список всех чатов (новые сверху)."""
    storage = _get_chat_storage()
    return storage.list_chats()


@router.post("/chats", status_code=201)
async def create_chat(data: dict = None, _=Depends(verify_jwt_token)):
    """
    Создаёт новый чат.
    Тело запроса (опционально): {"title": "Название чата"}
    Возвращает: {"chat_id": "...", "title": "...", "created_at": "..."}
    """
    storage = _get_chat_storage()
    
    # Извлекаем заголовок (если передан)
    title = None
    if data and isinstance(data, dict):
        title = data.get("title", "").strip()
        if not title:
            title = None
    
    # Проверяем максимальную длину заголовка (200 символов)
    if title and len(title) > 200:
        raise HTTPException(
            status_code=400,
            detail="Заголовок чата не должен превышать 200 символов"
        )
    
    result = storage.create_chat(title)
    return result


@router.get("/chats/{chat_id}")
async def get_chat(chat_id: str, _=Depends(verify_jwt_token)):
    """
    Возвращает чат с историей сообщений.
    Возвращает: {"chat_id": "...", "title": "...", "created_at": "...", "messages": [...]}
    """
    storage = _get_chat_storage()
    chat = storage.get_chat(chat_id)
    
    if chat is None:
        raise HTTPException(status_code=404, detail="Чат не найден")
    
    return chat


@router.patch("/chats/{chat_id}")
async def update_chat_title(chat_id: str, data: dict, _=Depends(verify_jwt_token)):
    """
    Переименовывает чат.
    Тело запроса: {"title": "Новое название"}
    Возвращает: {"status": "updated", "chat_id": "...", "title": "..."}
    """
    storage = _get_chat_storage()
    
    new_title = data.get("title", "").strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="Поле 'title' обязательно и не может быть пустым")
    
    if len(new_title) > 200:
        raise HTTPException(
            status_code=400,
            detail="Заголовок чата не должен превышать 200 символов"
        )
    
    success = storage.update_chat_title(chat_id, new_title)
    if not success:
        raise HTTPException(status_code=404, detail="Чат не найден")
    
    return {"status": "updated", "chat_id": chat_id, "title": new_title}


@router.delete("/chats/{chat_id}")
async def delete_chat(chat_id: str, _=Depends(verify_jwt_token)):
    """
    Удаляет чат и все его сообщения (каскадно).
    Возвращает: {"status": "deleted", "chat_id": "..."}
    """
    storage = _get_chat_storage()
    success = storage.delete_chat(chat_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Чат не найден")
    
    # Удаляем связанные записи из ChromaDB (если коллекция chat_messages заполнялась)
    try:
        from services.chroma.client import ChromaClient
        config = load_config()
        chroma_dir = config.project_root / "data" / "chroma"
        chroma_client = ChromaClient(chroma_dir)
        
        if chroma_client.available:
            chat_collection = chroma_client.chat_messages
            if chat_collection:
                # Удаляем все записи с метаданными chat_id
                # (пока коллекция пустая, но код готов к будущему заполнению)
                try:
                    all_docs = chat_collection.get(where={"chat_id": chat_id})
                    if all_docs and all_docs["ids"]:
                        chat_collection.delete(ids=all_docs["ids"])
                        logger.info(f"Удалено {len(all_docs['ids'])} записей из chat_messages для чата {chat_id}")
                except Exception as e:
                    logger.warning(f"Не удалось удалить записи из chat_messages: {e}")
    except Exception as e:
        logger.warning(f"Ошибка при очистке ChromaDB для чата {chat_id}: {e}")
    
    return {"status": "deleted", "chat_id": chat_id}
