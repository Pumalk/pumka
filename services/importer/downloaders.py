"""
services/importer/downloaders.py — скачивание видео (аудио+превью) и статей.
"""

import logging
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional

import httpx
import yt_dlp
from bs4 import BeautifulSoup
from readability import Document

logger = logging.getLogger("pumka.system")

# User-Agent браузера для маскировки при парсинге статей
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def download_video_audio(url: str, output_dir: Path) -> Dict[str, Any]:
    """
    Скачивает ТОЛЬКО аудио и превью из видео.

    Args:
        url: Ссылка на видео (YouTube и т.п.)
        output_dir: Папка для сохранения файлов

    Returns:
        Словарь с метаданными:
        {
            "title": str,
            "duration": int (секунды),
            "url": str,
            "audio_path": Path,
            "thumbnail_path": Optional[Path],
            "error": Optional[str]
        }
    """
    logger.info(f"Скачивание аудио из видео: {url}")

    # Настройки yt-dlp
    ydl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio",  # Только аудио
        "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "writethumbnail": True,  # Скачать превью
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Извлекаем метаданные
            info = ydl.extract_info(url, download=True)

            if not info:
                return {"error": "Не удалось извлечь информацию о видео"}

            video_id = info.get("id", "unknown")
            title = info.get("title", "Без названия")
            duration = info.get("duration", 0)

            # Ищем скачанный аудиофайл
            audio_path = None
            for ext in ["m4a", "webm", "opus", "mp3"]:
                candidate = output_dir / f"{video_id}.{ext}"
                if candidate.exists():
                    audio_path = candidate
                    break

            # Ищем превью
            thumbnail_path = None
            for ext in ["jpg", "png", "webp"]:
                candidate = output_dir / f"{video_id}.{ext}"
                if candidate.exists():
                    thumbnail_path = candidate
                    break

            logger.info(
                f"Скачано: title='{title}', duration={duration}s, "
                f"audio={audio_path}, thumbnail={thumbnail_path}"
            )

            return {
                "title": title,
                "duration": duration,
                "url": url,
                "audio_path": audio_path,
                "thumbnail_path": thumbnail_path,
                "error": None,
            }

    except Exception as e:
        logger.error(f"Ошибка скачивания видео: {e}")
        return {"error": str(e)}


def fetch_article(url: str) -> Dict[str, Any]:
    """
    Скачивает статью и извлекает текст.

    Args:
        url: Ссылка на статью

    Returns:
        Словарь:
        {
            "title": str,
            "text": str,
            "url": str,
            "preview_url": Optional[str],
            "error": Optional[str]
        }
    """
    logger.info(f"Скачивание статьи: {url}")

    try:
        # HTTP-запрос с User-Agent браузера
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(
                url,
                headers={"User-Agent": BROWSER_UA},
            )
            response.raise_for_status()

        # Определяем кодировку
        if response.encoding is None or response.encoding.lower() == "iso-8859-1":
            # chardet для определения кодировки
            import chardet

            detected = chardet.detect(response.content)
            encoding = detected.get("encoding", "utf-8")
        else:
            encoding = response.encoding

        html = response.content.decode(encoding, errors="replace")

        # Извлекаем текст через readability
        doc = Document(html)
        title = doc.title()
        text = doc.summary()

        # Если readability не дал текста — fallback на <body>
        if not text or len(text.strip()) < 100:
            soup = BeautifulSoup(html, "lxml")
            body = soup.find("body")
            if body:
                text = body.get_text(separator="\n", strip=True)
            else:
                text = soup.get_text(separator="\n", strip=True)

        # Извлекаем og:image для превью
        soup = BeautifulSoup(html, "lxml")
        og_image = soup.find("meta", property="og:image")
        preview_url = og_image.get("content") if og_image else None

        logger.info(
            f"Статья скачана: title='{title}', text_length={len(text)}, "
            f"preview_url={preview_url}"
        )

        return {
            "title": title,
            "text": text,
            "url": url,
            "preview_url": preview_url,
            "error": None,
        }

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP ошибка при скачивании статьи: {e.response.status_code}")
        return {"error": f"HTTP {e.response.status_code}"}

    except Exception as e:
        logger.error(f"Ошибка скачивания статьи: {e}")
        return {"error": str(e)}
