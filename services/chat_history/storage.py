"""
services/chat_history/storage.py — хранилище чатов GUI в SQLite.
Таблицы: chats (id, title, created_at) и messages (id, chat_id, role, content, timestamp).
Поддерживает каскадное удаление: при удалении чата удаляются все его сообщения.
"""
import sqlite3
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import uuid

logger = logging.getLogger("pumka.system")


class ChatStorage:
    """
    Хранилище чатов GUI в SQLite.
    Создаёт базу в указанной директории, инициализирует таблицы при первом запуске.
    """

    def __init__(self, db_dir: Path):
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.db_dir / "chats.db"
        self._init_db()

    def _init_db(self):
        """Инициализирует таблицы chats и messages."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Таблица чатов
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS chats (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)
                
                # Таблица сообщений с каскадным удалением
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chat_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
                    )
                """)
                
                # Индекс для быстрого поиска сообщений по chat_id
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_messages_chat_id 
                    ON messages(chat_id)
                """)
                
                conn.commit()
                logger.info(f"База данных чатов инициализирована: {self.db_path}")
        
        except Exception as e:
            logger.error(f"Ошибка при инициализации базы чатов: {e}")
            raise

    def _get_connection(self) -> sqlite3.Connection:
        """Возвращает соединение с базой данных с включённым каскадным удалением."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")  # Включаем каскадное удаление
        conn.row_factory = sqlite3.Row  # Возвращать строки как словари
        return conn

    def create_chat(self, title: Optional[str] = None) -> Dict[str, Any]:
        """
        Создаёт новый чат.
        Args:
            title: Заголовок чата. Если None или пустой — дефолтный "Новый чат YYYY-MM-DD HH:MM"
        Returns:
            {"chat_id": "...", "title": "...", "created_at": "..."}
        """
        chat_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        
        if not title or not title.strip():
            # Дефолтное название: "Новый чат YYYY-MM-DD HH:MM"
            now = datetime.now()
            title = f"Новый чат {now.strftime('%Y-%m-%d %H:%M')}"
        
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO chats (id, title, created_at) VALUES (?, ?, ?)",
                    (chat_id, title, created_at)
                )
                conn.commit()
            
            logger.info(f"Создан чат: {chat_id} ({title})")
            return {"chat_id": chat_id, "title": title, "created_at": created_at}
        
        except Exception as e:
            logger.error(f"Ошибка при создании чата: {e}")
            raise

    def list_chats(self) -> List[Dict[str, Any]]:
        """
        Возвращает список всех чатов с последним сообщением пользователя.
        Сортировка: новые сверху (по created_at DESC).
        Returns:
            [{"chat_id": "...", "title": "...", "created_at": "...", "last_message": "..."}, ...]
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Получаем все чаты
                cursor.execute("""
                    SELECT c.id, c.title, c.created_at,
                           (SELECT content FROM messages 
                            WHERE chat_id = c.id AND role = 'user' 
                            ORDER BY timestamp DESC LIMIT 1) as last_message
                    FROM chats c
                    ORDER BY c.created_at DESC
                """)
                
                rows = cursor.fetchall()
                chats = []
                
                for row in rows:
                    chat_id = row["id"]
                    title = row["title"]
                    created_at = row["created_at"]
                    last_message = row["last_message"]
                    
                    # Формируем last_message: первые 50 символов
                    if last_message:
                        last_message_preview = last_message[:50].replace("\n", " ")
                    else:
                        last_message_preview = "Без сообщений"
                    
                    chats.append({
                        "chat_id": chat_id,
                        "title": title,
                        "created_at": created_at,
                        "last_message": last_message_preview
                    })
                
                return chats
        
        except Exception as e:
            logger.error(f"Ошибка при получении списка чатов: {e}")
            raise

    def get_chat(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """
        Возвращает чат с историей сообщений.
        Args:
            chat_id: UUID чата
        Returns:
            {"chat_id": "...", "title": "...", "created_at": "...", "messages": [...]}
            или None, если чат не найден
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Получаем чат
                cursor.execute(
                    "SELECT id, title, created_at FROM chats WHERE id = ?",
                    (chat_id,)
                )
                row = cursor.fetchone()
                
                if not row:
                    return None
                
                chat_id = row["id"]
                title = row["title"]
                created_at = row["created_at"]
                
                # Получаем сообщения
                cursor.execute("""
                    SELECT role, content, timestamp 
                    FROM messages 
                    WHERE chat_id = ? 
                    ORDER BY timestamp ASC
                """, (chat_id,))
                
                messages = [
                    {
                        "role": msg["role"],
                        "content": msg["content"],
                        "timestamp": msg["timestamp"]
                    }
                    for msg in cursor.fetchall()
                ]
                
                return {
                    "chat_id": chat_id,
                    "title": title,
                    "created_at": created_at,
                    "messages": messages
                }
        
        except Exception as e:
            logger.error(f"Ошибка при получении чата {chat_id}: {e}")
            raise

    def update_chat_title(self, chat_id: str, new_title: str) -> bool:
        """
        Обновляет заголовок чата.
        Args:
            chat_id: UUID чата
            new_title: Новый заголовок
        Returns:
            True, если обновлено успешно, False, если чат не найден
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE chats SET title = ? WHERE id = ?",
                    (new_title, chat_id)
                )
                conn.commit()
                
                if cursor.rowcount == 0:
                    logger.warning(f"Чат {chat_id} не найден для обновления заголовка")
                    return False
                
                logger.info(f"Заголовок чата {chat_id} обновлён: {new_title}")
                return True
        
        except Exception as e:
            logger.error(f"Ошибка при обновлении заголовка чата {chat_id}: {e}")
            raise

    def delete_chat(self, chat_id: str) -> bool:
        """
        Удаляет чат и все его сообщения (каскадно).
        Args:
            chat_id: UUID чата
        Returns:
            True, если удалено успешно, False, если чат не найден
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
                conn.commit()
                
                if cursor.rowcount == 0:
                    logger.warning(f"Чат {chat_id} не найден для удаления")
                    return False
                
                logger.info(f"Чат {chat_id} удалён")
                return True
        
        except Exception as e:
            logger.error(f"Ошибка при удалении чата {chat_id}: {e}")
            raise

    def add_message(self, chat_id: str, role: str, content: str) -> str:
        """
        Добавляет сообщение в чат.
        Args:
            chat_id: UUID чата
            role: "user" или "assistant"
            content: Текст сообщения
        Returns:
            Timestamp сообщения в ISO 8601 UTC
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO messages (chat_id, role, content, timestamp)
                    VALUES (?, ?, ?, ?)
                """, (chat_id, role, content, timestamp))
                conn.commit()
            
            return timestamp
        
        except Exception as e:
            logger.error(f"Ошибка при добавлении сообщения в чат {chat_id}: {e}")
            raise

    def get_history(self, chat_id: str, limit: int = 20) -> List[Dict[str, str]]:
        """
        Возвращает последние N сообщений чата (для контекста LLM).
        Args:
            chat_id: UUID чата
            limit: Максимальное количество сообщений
        Returns:
            [{"role": "...", "content": "..."}, ...]
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT role, content 
                    FROM messages 
                    WHERE chat_id = ? 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                """, (chat_id, limit))
                
                # Переворачиваем, чтобы старые сообщения были первыми
                messages = [
                    {"role": msg["role"], "content": msg["content"]}
                    for msg in reversed(cursor.fetchall())
                ]
                
                return messages
        
        except Exception as e:
            logger.error(f"Ошибка при получении истории чата {chat_id}: {e}")
            raise
