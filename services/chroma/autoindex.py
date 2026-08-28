"""
services/chroma/autoindex.py — управление индексацией Vault.
Предотвращает одновременный запуск нескольких индексаций (409 Conflict).
Поддерживает синхронный и фоновый запуск.
"""
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from services.chroma.client import ChromaClient
from services.chroma.indexer import index_vault

logger = logging.getLogger("pumka.system")

# Флаг для предотвращения одновременной индексации
_is_indexing = False
_indexing_lock = asyncio.Lock()


async def run_index_vault(
    vault_root: Path,
    chroma_client: ChromaClient,
    max_file_size_mb: int = 10,
    background: bool = False
) -> Dict[str, Any]:
    """
    Запускает индексацию Vault.
    
    Args:
        vault_root: Путь к корню Obsidian Vault
        chroma_client: ChromaClient с доступной коллекцией vault_notes
        max_file_size_mb: Максимальный размер файла в МБ
        background: Если True — запускает в фоне через asyncio.to_thread
                   Если False — выполняет синхронно (блокирует)
    
    Returns:
        {
            "status": "ok" | "already_running",
            "indexed": int,
            "errors": List[Dict[str, str]]
        }
    """
    global _is_indexing
    
    async with _indexing_lock:
        if _is_indexing:
            logger.warning("Индексация уже выполняется, возврат 409")
            return {
                "status": "already_running",
                "indexed": 0,
                "errors": [{"path": "", "error": "Индексация уже выполняется"}]
            }
        _is_indexing = True
    
    try:
        logger.info(f"Запуск индексации Vault: {vault_root} (background={background})")
        
        if background:
            # Запуск в фоне через asyncio.to_thread
            result = await asyncio.to_thread(
                index_vault,
                vault_root=vault_root,
                chroma_client=chroma_client,
                max_file_size_mb=max_file_size_mb
            )
        else:
            # Синхронный запуск
            result = index_vault(
                vault_root=vault_root,
                chroma_client=chroma_client,
                max_file_size_mb=max_file_size_mb
            )
        
        return {
            "status": "ok",
            "indexed": result["indexed"],
            "errors": result["errors"]
        }
    
    except Exception as e:
        logger.error(f"Ошибка при индексации Vault: {e}")
        return {
            "status": "ok",
            "indexed": 0,
            "errors": [{"path": "", "error": f"{type(e).__name__}: {e}"}]
        }
    
    finally:
        async with _indexing_lock:
            _is_indexing = False
            logger.info("Индексация завершена, флаг сброшен")
