"""
services/importer/notes.py — создание заметок в Obsidian Vault.
Дедупликация, теги, тематики, защита терминов.
"""

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

logger = logging.getLogger("pumka.system")

# Стандартные тематики
DEFAULT_TOPICS = [
    "Нейросети",
    "Программирование",
    "Инструменты",
    "Дизайн",
    "Безопасность",
    "Другие тематики",
]


def normalize_url(url: str) -> str:
    """
    Нормализует URL для дедупликации:
    - http → https
    - нижний регистр домена
    - убрать trailing slash
    - убрать UTM и трекерные параметры
    - убрать фрагмент (#...)
    """
    parsed = urlparse(url)

    # http → https
    scheme = "https" if parsed.scheme in ["http", "https"] else parsed.scheme

    # нижний регистр домена
    netloc = parsed.netloc.lower()

    # Убираем трекерные параметры
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    filtered_params = {
        k: v
        for k, v in query_params.items()
        if not k.startswith(("utm_", "fbclid", "gclid", "yclid", "ref"))
    }
    query = urlencode(filtered_params, doseq=True)

    # Убираем фрагмент
    normalized = urlunparse((scheme, netloc, parsed.path, parsed.params, query, ""))

    # Убираем trailing slash
    if normalized.endswith("/") and normalized.count("/") > 2:
        normalized = normalized[:-1]

    return normalized


def compute_hash(content: str) -> str:
    """Вычисляет SHA256 хеш от нормализованного контента."""
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()


def slugify(text: str, date: datetime) -> str:
    """
    Создаёт slug из текста: транслит + дата.
    Только буквы (ру/англ), цифры, пробел, дефис.
    """
    # Простая транслитерация (заменяем кириллицу на латиницу)
    translit_map = {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "yo",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }

    text_lower = text.lower()
    result = []
    for char in text_lower:
        if char in translit_map:
            result.append(translit_map[char])
        elif char.isalnum() or char in " -":
            result.append(char)
        else:
            result.append("-")

    # Заменяем множественные пробелы/дефисы на один дефис
    slug = re.sub(r"[\s-]+", "-", "".join(result)).strip("-")

    # Ограничиваем длину
    if len(slug) > 50:
        slug = slug[:50].rstrip("-")

    # Добавляем дату
    date_str = date.strftime("%Y%m%d")
    return f"{slug}-{date_str}" if slug else date_str


def load_json_file(path: Path, default: Any) -> Any:
    """Загружает JSON файл или возвращает default если файл не существует."""
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки {path}: {e}")
        return default


def save_json_file(path: Path, data: Any) -> None:
    """Сохраняет данные в JSON файл."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения {path}: {e}")


def check_duplicate(
    data_dir: Path,
    url: Optional[str] = None,
    content: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Проверяет дубликат по URL или хешу контента.

    Returns:
        Запись из imported.json если дубликат найден, иначе None
    """
    imported_path = data_dir / "memory" / "imported.json"
    imported = load_json_file(imported_path, {})

    if url:
        normalized = normalize_url(url)
        hash_key = compute_hash(normalized)
    elif content:
        hash_key = compute_hash(content)
    else:
        return None

    return imported.get(hash_key)


def mark_as_processing(
    data_dir: Path,
    url: Optional[str] = None,
    content: Optional[str] = None,
    title: str = "Без названия",
) -> str:
    """
    Помечает импорт как "processing" для crash recovery.
    Возвращает hash_key.
    """
    imported_path = data_dir / "memory" / "imported.json"
    imported = load_json_file(imported_path, {})

    if url:
        normalized = normalize_url(url)
        hash_key = compute_hash(normalized)
    elif content:
        hash_key = compute_hash(content)
    else:
        raise ValueError("Нужно указать url или content")

    imported[hash_key] = {
        "date": datetime.now().isoformat(),
        "title": title,
        "status": "processing",
        "source": url or "text",
    }

    save_json_file(imported_path, imported)
    logger.info(f"Помечено как processing: {hash_key}")
    return hash_key


def mark_as_done(
    data_dir: Path,
    hash_key: str,
    title: str,
    note_path: str,
) -> None:
    """Помечает импорт как завершённый."""
    imported_path = data_dir / "memory" / "imported.json"
    imported = load_json_file(imported_path, {})

    if hash_key in imported:
        imported[hash_key].update(
            {
                "status": "done",
                "title": title,
                "note_path": note_path,
            }
        )
        save_json_file(imported_path, imported)
        logger.info(f"Помечено как done: {hash_key}")


def load_tags(data_dir: Path) -> List[str]:
    """Загружает список существующих тегов."""
    tags_path = data_dir / "memory" / "tags.json"
    return load_json_file(tags_path, [])


def save_tags(data_dir: Path, tags: List[str]) -> None:
    """Сохраняет список тегов."""
    tags_path = data_dir / "memory" / "tags.json"
    save_json_file(tags_path, tags)


def add_new_tags(data_dir: Path, new_tags: List[str]) -> None:
    """Добавляет новые теги в tags.json."""
    existing = load_tags(data_dir)
    updated = list(set(existing + new_tags))
    save_tags(data_dir, updated)
    logger.info(f"Добавлено {len(new_tags)} новых тегов")


def load_protected_terms(data_dir: Path) -> List[str]:
    """Загружает список защищённых терминов."""
    terms_path = data_dir / "memory" / "protected_terms.txt"
    if not terms_path.exists():
        # Создаём стартовый список
        default_terms = [
            "Pumka",
            "Pumalk",
            "Ollama",
            "Docker",
            "Whisper",
            "FFmpeg",
            "Telegram",
            "Obsidian",
            "Chroma",
            "Redis",
            "Python",
            "PyQt6",
            "FastAPI",
            "aiogram",
            "yt-dlp",
            "easyocr",
        ]
        terms_path.parent.mkdir(parents=True, exist_ok=True)
        with open(terms_path, "w", encoding="utf-8") as f:
            f.write("\n".join(default_terms))
        return default_terms

    try:
        with open(terms_path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except Exception as e:
        logger.error(f"Ошибка загрузки {terms_path}: {e}")
        return []


def get_existing_topics(vault_path: Path) -> List[str]:
    """Получает список существующих тематик (папок в Vault)."""
    topics = []
    for topic in DEFAULT_TOPICS:
        topic_dir = vault_path / topic
        if topic_dir.exists() and topic_dir.is_dir():
            topics.append(topic)

    # Добавляем пользовательские папки
    for item in vault_path.iterdir():
        if item.is_dir() and item.name not in DEFAULT_TOPICS:
            if item.name not in ["Pumka", "media"]:
                topics.append(item.name)

    return topics


def ensure_topic_folder(vault_path: Path, topic: str) -> Path:
    """
    Создаёт папку для тематики если её нет.
    Проверяет безопасность пути.
    """
    # Фильтр имени: только буквы, цифры, пробел, дефис
    if not re.match(r"^[\w\s-]+$", topic):
        logger.warning(f"Некорректное имя тематики: {topic}")
        topic = "Другие тематики"

    if len(topic) > 50:
        topic = topic[:50]

    topic_dir = vault_path / topic

    # Проверка безопасности (realpath)
    try:
        topic_dir_resolved = topic_dir.resolve()
        vault_resolved = vault_path.resolve()
        if not str(topic_dir_resolved).startswith(str(vault_resolved)):
            logger.error(f"Попытка выхода за пределы vault: {topic_dir}")
            topic_dir = vault_path / "Другие тематики"
    except Exception as e:
        logger.error(f"Ошибка проверки пути: {e}")
        topic_dir = vault_path / "Другие тематики"

    topic_dir.mkdir(parents=True, exist_ok=True)
    return topic_dir


def create_note(
    vault_path: Path,
    data_dir: Path,
    title: str,
    source: str,
    content_type: str,
    topic: str,
    tags: List[str],
    summary: str,
    transcript: Optional[str] = None,
    media_path: Optional[str] = None,
) -> Path:
    """
    Создаёт заметку в Vault по шаблону.

    Returns:
        Путь к созданной заметке
    """
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d %H:%M")

    # Создаём slug для имени файла
    slug = slugify(title, now)

    # Определяем папку для заметки
    topic_dir = ensure_topic_folder(vault_path, topic)

    # Путь к заметке
    note_path = topic_dir / f"{slug}.md"

    # Проверяем безопасность пути
    try:
        note_path_resolved = note_path.resolve()
        vault_resolved = vault_path.resolve()
        if not str(note_path_resolved).startswith(str(vault_resolved)):
            logger.error(f"Попытка выхода за пределы vault: {note_path}")
            note_path = vault_path / "Другие тематики" / f"{slug}.md"
    except Exception as e:
        logger.error(f"Ошибка проверки пути: {e}")
        note_path = vault_path / "Другие тематики" / f"{slug}.md"

    # Формируем теги
    tags_str = " ".join([f"#{tag}" for tag in tags]) if tags else ""

    # Формируем содержимое заметки
    note_content = f"""# {title}
- Дата: {date_str}
- Источник: {source}
- Тип: {content_type}
- Тематика: {topic}
- Теги: {tags_str}

## Описание
{summary}

## Транскрипт
{transcript if transcript else "нет"}

## Медиа
{media_path if media_path else "нет"}
"""

    # Сохраняем заметку
    try:
        note_path.parent.mkdir(parents=True, exist_ok=True)
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(note_content)
        logger.info(f"Заметка создана: {note_path}")
        return note_path
    except Exception as e:
        logger.error(f"Ошибка создания заметки: {e}")
        raise


def create_transcript_file(
    vault_path: Path,
    slug: str,
    transcript: str,
) -> Path:
    """
    Создаёт отдельный файл транскрипта в media/documents/.

    Returns:
        Путь к файлу транскрипта
    """
    documents_dir = vault_path / "media" / "documents"
    documents_dir.mkdir(parents=True, exist_ok=True)

    transcript_path = documents_dir / f"{slug}_transcript.md"

    try:
        with open(transcript_path, "w", encoding="utf-8") as f:
            f.write(f"# Транскрипт\n\n{transcript}\n")
        logger.info(f"Файл транскрипта создан: {transcript_path}")
        return transcript_path
    except Exception as e:
        logger.error(f"Ошибка создания файла транскрипта: {e}")
        raise
