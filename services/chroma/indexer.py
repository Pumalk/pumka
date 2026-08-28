"""
services/chroma/indexer.py — индексатор заметок Obsidian Vault.
Читает все .md файлы из vault_path, парсит метаданные,
создаёт эмбеддинги и сохраняет в ChromaDB коллекцию vault_notes.
Поддерживает full sync: удаляет записи, которых больше нет на диске.
"""
import os
import hashlib
import logging
from pathlib import Path
from typing import List, Dict, Any, Set
from services.chroma.client import ChromaClient, VAULT_NOTES_COLLECTION
from services.chroma.parser import parse_note_metadata

logger = logging.getLogger("pumka.system")

# Папки, которые исключаем на любом уровне вложенности
EXCLUDED_FOLDERS = {"media", ".obsidian", "templates"}

# Максимальный размер саммари для эмбеддинга (согласовано: 1000 символов)
MAX_EMBEDDING_LENGTH = 1000


def _compute_file_id(file_path: Path) -> str:
    """Вычисляет SHA-256 хэш от пути файла (hex digest)."""
    path_str = str(file_path)
    return hashlib.sha256(path_str.encode("utf-8")).hexdigest()


def _should_exclude_dir(dir_path: Path) -> bool:
    """Проверяет, нужно ли исключить директорию из индексации."""
    for part in dir_path.parts:
        if part in EXCLUDED_FOLDERS:
            return True
    return False


def _find_md_files(vault_root: Path) -> List[Path]:
    """
    Рекурсивно находит все .md файлы в vault, исключая EXCLUDED_FOLDERS.
    Возвращает список путей.
    """
    md_files = []
    for root, dirs, files in os.walk(vault_root):
        root_path = Path(root)
        
        # Проверяем, не нужно ли исключить эту директорию
        if _should_exclude_dir(root_path.relative_to(vault_root)):
            continue
        
        for file_name in files:
            if file_name.lower().endswith(".md"):
                file_path = root_path / file_name
                md_files.append(file_path)
    
    return md_files


def index_vault(
    vault_root: Path,
    chroma_client: ChromaClient,
    max_file_size_mb: int = 10
) -> Dict[str, Any]:
    """
    Индексирует все .md файлы из vault в ChromaDB коллекцию vault_notes.
    
    Args:
        vault_root: Путь к корню Obsidian Vault
        chroma_client: ChromaClient с доступной коллекцией vault_notes
        max_file_size_mb: Максимальный размер файла в МБ (пропускать большие)
    
    Returns:
        {
            "indexed": int,  # количество успешно обработанных файлов
            "errors": List[Dict[str, str]]  # список ошибок {path, error}
        }
    """
    if not chroma_client.available:
        error_msg = "ChromaDB недоступен"
        logger.error(f"Индексация пропущена: {error_msg}")
        return {"indexed": 0, "errors": [{"path": "", "error": error_msg}]}
    
    vault_collection = chroma_client.vault_notes
    if vault_collection is None:
        error_msg = "Не удалось получить коллекцию vault_notes"
        logger.error(f"Индексация пропущена: {error_msg}")
        return {"indexed": 0, "errors": [{"path": "", "error": error_msg}]}
    
    vault_root = Path(vault_root).resolve()
    if not vault_root.exists():
        error_msg = f"Vault не найден: {vault_root}"
        logger.error(f"Индексация пропущена: {error_msg}")
        return {"indexed": 0, "errors": [{"path": "", "error": error_msg}]}
    
    logger.info(f"Начата индексация Vault: {vault_root}")
    
    # Находим все .md файлы
    md_files = _find_md_files(vault_root)
    logger.info(f"Найдено {len(md_files)} .md файлов для индексации")
    
    max_file_size_bytes = max_file_size_mb * 1024 * 1024
    
    indexed_count = 0
    errors = []
    indexed_ids = set()  # ID всех успешно проиндексированных файлов
    
    for file_path in md_files:
        try:
            # Проверяем размер файла
            file_size = file_path.stat().st_size
            if file_size > max_file_size_bytes:
                logger.warning(f"Пропуск большого файла ({file_size} bytes): {file_path}")
                continue
            
            # Читаем файл
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError as e:
                error_msg = f"Не UTF-8 кодировка: {e}"
                logger.warning(f"Пропуск файла {file_path}: {error_msg}")
                errors.append({"path": str(file_path), "error": error_msg})
                continue
            
            # Пропускаем пустые файлы
            if not content.strip():
                logger.debug(f"Пропуск пустого файла: {file_path}")
                continue
            
            # Парсим метаданные
            metadata = parse_note_metadata(content, file_path, vault_root)
            
            # Формируем текст для эмбеддинга: title + summary
            embedding_text = f"{metadata['title']} {metadata['summary']}".strip()
            if len(embedding_text) > MAX_EMBEDDING_LENGTH:
                embedding_text = embedding_text[:MAX_EMBEDDING_LENGTH]
            
            # Если текст для эмбеддинга пустой — пропускаем
            if not embedding_text.strip():
                logger.warning(f"Пропуск файла без текста для эмбеддинга: {file_path}")
                continue
            
            # Вычисляем ID
            file_id = _compute_file_id(file_path)
            
            # Подготавливаем метаданные для ChromaDB
            # ChromaDB требует плоский словарь метаданных
            chroma_metadata = {
                "path": metadata["path"],
                "title": metadata["title"],
                "tags": ",".join(metadata["tags"]) if metadata["tags"] else "",  # список → строка
                "topic": metadata["topic"],
                "date": metadata["date"],
                "source": metadata["source"],
                "type": metadata["type"],
            }
            
            # Сохраняем в ChromaDB (upsert = обновить если есть, добавить если нет)
            vault_collection.upsert(
                ids=[file_id],
                documents=[embedding_text],
                metadatas=[chroma_metadata]
            )
            
            indexed_ids.add(file_id)
            indexed_count += 1
            
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.error(f"Ошибка при индексации {file_path}: {error_msg}")
            errors.append({"path": str(file_path), "error": error_msg})
    
    # Full sync: удаляем записи, которых больше нет на диске
    logger.info("Проверка записей для удаления (full sync)...")
    try:
        # Получаем все ID из коллекции
        all_docs = vault_collection.get()
        if all_docs and all_docs["ids"]:
            existing_ids = set(all_docs["ids"])
            ids_to_delete = existing_ids - indexed_ids
            
            if ids_to_delete:
                logger.info(f"Удаление {len(ids_to_delete)} записей, которых больше нет на диске")
                vault_collection.delete(ids=list(ids_to_delete))
    except Exception as e:
        logger.error(f"Ошибка при full sync: {e}")
        errors.append({"path": "", "error": f"Full sync error: {e}"})
    
    logger.info(f"Индексация завершена: {indexed_count} файлов, {len(errors)} ошибок")
    
    return {"indexed": indexed_count, "errors": errors}
