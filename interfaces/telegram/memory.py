"""
interfaces/telegram/memory.py — in-memory память диалога.
Хранит последние MEMORY_LIMIT сообщений для каждого пользователя.
"""
import logging
from typing import Dict, List

logger = logging.getLogger("pumka.system")

# Память диалога: последние 20 сообщений
MEMORY_LIMIT = 20

# Структура:
# {
#     user_id: [
#         {"role": "user", "text": "..."},
#         {"role": "bot", "text": "..."},
#         ...
#     ]
# }
#
# Важно:
# - память живёт только в оперативной памяти;
# - при перезапуске бота память теряется;
# - полноценная долгосрочная память будет позже, на Этапе 5.
chat_memory: Dict[int, list] = {}


def add_message_to_memory(
    chat_memory: Dict[int, List[Dict[str, str]]],
    user_id: int,
    role: str,
    text: str,
) -> None:
    """
    Добавляет сообщение в память диалога.
    role: "user" или "bot"
    """
    clean_text = (text or "").strip()
    if not clean_text:
        return
    history = chat_memory.setdefault(user_id, [])
    history.append({"role": role, "text": clean_text})


def trim_memory(
    chat_memory: Dict[int, List[Dict[str, str]]],
    user_id: int,
) -> None:
    """
    Оставляет только последние MEMORY_LIMIT сообщений.
    """
    history = chat_memory.get(user_id)
    if not history:
        return
    if len(history) > MEMORY_LIMIT:
        history[:] = history[-MEMORY_LIMIT:]
        logger.info(
            f"Память обрезана до {len(history)} сообщений для пользователя {user_id}"
        )


def build_history_block(history: List[Dict[str, str]]) -> str:
    """
    Строит блок истории диалога для system_prompt.
    Если история пустая — блок не добавляется.
    """
    if not history:
        return ""
    lines = ["== ИСТОРИЯ ДИАЛОГА (последние сообщения) =="]
    for item in history:
        role = item.get("role", "")
        text = item.get("text", "")
        if role == "user":
            lines.append(f"Пользователь: {text}")
        else:
            lines.append(f"Бот: {text}")
    return "\n".join(lines)
