"""
services/chroma/parser.py — парсер заметок Obsidian Vault.
Извлекает метаданные из .md файлов: заголовок, саммари, теги, тематику, дату.
Поддерживает два формата: структурированный (с полями Дата:, Тематика: и т.д.)
и свободный (дата первой строкой, заголовок в тексте).
"""
import re
import os
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

logger = logging.getLogger("pumka.system")

# Максимальная длина саммари для эмбеддинга (согласовано: 1000 символов)
MAX_SUMMARY_LENGTH = 1000

# Максимальная длина заголовка (согласовано: 100 символов)
MAX_TITLE_LENGTH = 100

# Заголовки разделов, которые НЕ являются заголовком заметки
SECTION_HEADERS = {"описание", "теги", "транскрипт", "медиа"}

# Поля метаданных (для распознавания)
METADATA_PREFIXES = ("дата:", "источник:", "тип:", "тематика:", "теги:")

# Регулярка для тегов: #слово (буквы, цифры, -, _)
# Поддерживает латиницу, кириллицу, цифры, дефис, подчёркивание
TAG_PATTERN = re.compile(r"#([\wа-яА-ЯёЁ-]+)")

# Регулярки для дат: (паттерн, формат)
DATE_PATTERNS = [
    # 2026-08-25 19:04
    (r"(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2})", "%Y-%m-%d %H:%M"),
    # 2026-08-25
    (r"(\d{4}-\d{2}-\d{2})", "%Y-%m-%d"),
    # 21.01.2026
    (r"(\d{2}\.\d{2}\.\d{4})", "%d.%m.%Y"),
]


def _is_date_line(line: str) -> bool:
    """Проверяет, является ли строка датой в одном из поддерживаемых форматов."""
    line = line.strip()
    for pattern, _ in DATE_PATTERNS:
        if re.fullmatch(pattern, line):
            return True
    return False


def _parse_date_string(date_str: str) -> Optional[datetime]:
    """Парсит строку даты в объект datetime (локальное время сервера)."""
    date_str = date_str.strip()
    for pattern, fmt in DATE_PATTERNS:
        match = re.search(pattern, date_str)
        if match:
            try:
                return datetime.strptime(match.group(1), fmt)
            except ValueError:
                continue
    return None


def _to_utc_isoformat(dt: datetime) -> str:
    """Конвертирует datetime в ISO 8601 UTC строку."""
    # Считаем локальным временем сервера, конвертируем в UTC
    dt_local = dt.astimezone()
    return dt_local.astimezone(timezone.utc).isoformat()


def parse_note_date(content: str, file_path: Path) -> str:
    """
    Извлекает дату заметки в формате ISO 8601 UTC.
    Приоритет: поле 'Дата:' > первая строка > имя файла > дата модификации.
    """
    lines = content.split("\n")
    
    # 1. Ищем поле 'Дата:' в первых 20 строках
    for line in lines[:20]:
        if line.strip().lower().startswith("дата:"):
            date_part = line.split(":", 1)[1].strip()
            dt = _parse_date_string(date_part)
            if dt:
                return _to_utc_isoformat(dt)
    
    # 2. Проверяем первую строку
    if lines:
        first_line = lines[0].strip()
        if _is_date_line(first_line):
            dt = _parse_date_string(first_line)
            if dt:
                return _to_utc_isoformat(dt)
    
    # 3. Проверяем имя файла на дату
    file_name = file_path.stem
    dt = _parse_date_string(file_name)
    if dt:
        return _to_utc_isoformat(dt)
    
    # 4. Дата модификации файла
    try:
        mtime = os.path.getmtime(file_path)
        dt = datetime.fromtimestamp(mtime)
        return _to_utc_isoformat(dt)
    except OSError:
        return datetime.now(timezone.utc).isoformat()


def _is_section_header(line: str) -> bool:
    """Проверяет, является ли строка заголовком раздела (Описание, Теги и т.д.)."""
    return line.strip().lower() in SECTION_HEADERS


def _is_metadata_line(line: str) -> bool:
    """Проверяет, является ли строка полем метаданных (Дата:, Источник: и т.д.)."""
    line = line.strip().lower()
    return line.startswith(METADATA_PREFIXES)


def _is_media_line(line: str) -> bool:
    """Проверяет, является ли строка картинкой, ссылкой или медиа."""
    line = line.strip()
    return (
        line.startswith("![[") or  # картинка Obsidian
        line.startswith("http://") or
        line.startswith("https://") or
        line.startswith("⛓")  # ссылка с эмодзи
    )


def parse_note_title(content: str, file_path: Path) -> str:
    """
    Извлекает заголовок заметки.
    Логика: первая строка, которая не является датой, заголовком раздела,
    полем метаданных, картинкой или ссылкой. Если ничего не нашли — имя файла.
    Обрезается до 100 символов.
    """
    lines = content.split("\n")
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _is_date_line(stripped):
            continue
        if _is_section_header(stripped):
            continue
        if _is_metadata_line(stripped):
            continue
        if _is_media_line(stripped):
            continue
        
        # Нашли заголовок
        title = stripped
        # Убираем суффиксы источника (например, "/ Хабр", "| Источник")
        title = re.split(r"\s+/\s+", title)[0]
        title = re.split(r"\s+\|\s+", title)[0]
        title = title.strip()
        
        # Обрезаем до 100 символов
        if len(title) > MAX_TITLE_LENGTH:
            title = title[:MAX_TITLE_LENGTH] + "..."
        
        return title
    
    # Если ничего не нашли — имя файла без расширения
    return file_path.stem


def parse_note_summary(content: str) -> str:
    """
    Извлекает саммари (описание) заметки.
    Логика: раздел 'Описание' целиком. Если нет — первый абзац основного текста.
    Обрезается до 1000 символов.
    """
    lines = content.split("\n")
    
    # Ищем раздел 'Описание'
    in_description = False
    description_lines = []
    
    for line in lines:
        stripped = line.strip()
        
        # Начало раздела 'Описание'
        if stripped.lower() == "описание":
            in_description = True
            continue
        
        # Конец раздела: другой заголовок раздела или поле метаданных
        if in_description:
            if _is_section_header(stripped) or _is_metadata_line(stripped):
                break
            description_lines.append(line)
    
    if description_lines:
        summary = "\n".join(description_lines).strip()
        # Убираем картинки, ссылки и медиа из саммари
        summary = re.sub(r"!\[\[.*?\]\]", "", summary)
        summary = re.sub(r"https?://\S+", "", summary)
        summary = re.sub(r"⛓.*", "", summary)
        summary = summary.strip()
        if len(summary) > MAX_SUMMARY_LENGTH:
            summary = summary[:MAX_SUMMARY_LENGTH]
        return summary
    
    # Если раздела 'Описание' нет — первый абзац основного текста
    paragraph_lines = []
    started = False
    
    for line in lines:
        stripped = line.strip()
        
        if not stripped and not started:
            continue
        
        if _is_date_line(stripped) or _is_section_header(stripped) or _is_metadata_line(stripped) or _is_media_line(stripped):
            if started:
                break
            continue
        
        started = True
        if not stripped:
            break
        paragraph_lines.append(stripped)
    
    summary = " ".join(paragraph_lines).strip()
    if len(summary) > MAX_SUMMARY_LENGTH:
        summary = summary[:MAX_SUMMARY_LENGTH]
    return summary


def parse_note_tags(content: str) -> List[str]:
    """
    Извлекает теги из всего текста заметки.
    Регулярка: #слово (буквы, цифры, -, _).
    Игнорирует теги внутри блоков кода и ссылок.
    Возвращает список тегов в нижнем регистре, без символа #.
    """
    # Убираем блоки кода (```...```)
    content_no_code = re.sub(r"```.*?```", "", content, flags=re.DOTALL)
    # Убираем ссылки (чтобы не парсить теги из якорей)
    content_no_code = re.sub(r"https?://\S+", "", content_no_code)
    
    # Ищем все теги
    tags = TAG_PATTERN.findall(content_no_code)
    
    # Приводим к нижнему регистру, убираем дубликаты, сохраняем порядок
    seen = set()
    unique_tags = []
    for tag in tags:
        tag_lower = tag.lower()
        if tag_lower not in seen:
            seen.add(tag_lower)
            unique_tags.append(tag_lower)
    
    return unique_tags


def parse_note_topic(content: str, file_path: Path, vault_root: Path) -> str:
    """
    Извлекает тематику заметки.
    Приоритет: поле 'Тематика:' (первое значение до запятой)
    > имя родительской папки > 'Другие тематики'.
    """
    lines = content.split("\n")
    
    # Ищем поле 'Тематика:' в первых 20 строках
    for line in lines[:20]:
        if line.strip().lower().startswith("тематика:"):
            topic_part = line.split(":", 1)[1].strip()
            topic = topic_part.split(",")[0].strip()
            if topic:
                return topic
    
    # Если нет поля — имя родительской папки
    try:
        relative_path = file_path.relative_to(vault_root)
        parent_folder = relative_path.parent
        if parent_folder != Path("."):
            return parent_folder.parts[0]
    except (ValueError, IndexError):
        pass
    
    # Если заметка в корне — 'Другие тематики'
    return "Другие тематики"


def parse_note_metadata(content: str, file_path: Path, vault_root: Path) -> Dict[str, Any]:
    """
    Собирает все метаданные заметки в один словарь.
    Возвращает: path, title, tags, topic, date, source, type, summary.
    """
    lines = content.split("\n")
    
    # Извлекаем источник и тип из полей (если есть)
    source = ""
    note_type = ""
    for line in lines[:20]:
        stripped = line.strip()
        if stripped.lower().startswith("источник:"):
            source = stripped.split(":", 1)[1].strip()
        elif stripped.lower().startswith("тип:"):
            note_type = stripped.split(":", 1)[1].strip()
    
    return {
        "path": str(file_path),
        "title": parse_note_title(content, file_path),
        "tags": parse_note_tags(content),
        "topic": parse_note_topic(content, file_path, vault_root),
        "date": parse_note_date(content, file_path),
        "source": source,
        "type": note_type,
        "summary": parse_note_summary(content),
    }
