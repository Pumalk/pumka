"""
services/chroma/client.py — обёртка над ChromaDB.

Создаёт и предоставляет доступ к двум коллекциям:
- vault_notes: индекс заметок Obsidian Vault
- chat_messages: заготовка для истории чатов GUI
  (реальное заполнение отложено на Этап 6, после RQ-очереди)

Использует встроенные эмбеддинги ChromaDB (all-MiniLM-L6-v2),
чтобы не тянуть тяжёлый sentence-transformers.
"""
import logging
from pathlib import Path
import chromadb

logger = logging.getLogger("pumka.system")

# Имена коллекций (константы, чтобы не опечататься)
VAULT_NOTES_COLLECTION = "vault_notes"
CHAT_MESSAGES_COLLECTION = "chat_messages"


class ChromaClient:
    """
    Обёртка над PersistentClient ChromaDB.
    
    Создаёт клиента один раз, коллекции — лениво при первом обращении.
    Если что-то пошло не так при создании клиента — помечает себя как
    недоступный (available=False), и все обращения к коллекциям вернут None.
    Это нужно, чтобы падение ChromaDB не роняло весь сервер (согласовано:
    сервер продолжает работать с WARNING в логах).
    """

    def __init__(self, chroma_dir: Path):
        self.chroma_dir = Path(chroma_dir)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        self._client = None
        
        try:
            self._client = chromadb.PersistentClient(path=str(self.chroma_dir))
            logger.info(f"ChromaDB клиент создан: {self.chroma_dir}")
        except Exception as e:
            logger.error(
                f"Не удалось создать ChromaDB клиент в {self.chroma_dir}: {e}. "
                f"ChromaDB будет недоступен до перезапуска."
            )

    @property
    def available(self) -> bool:
        """True, если ChromaDB доступен, False — если не удалось создать клиент."""
        return self._client is not None

    def _get_or_create(self, name: str):
        """
        Возвращает коллекцию по имени, создавая её при первом обращении.
        Если ChromaDB недоступен — возвращает None.
        """
        if not self.available:
            return None
        try:
            # get_or_create_collection использует встроенный embedder
            # (all-MiniLM-L6-v2) по умолчанию
            collection = self._client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"}  # косинусная метрика расстояний
            )
            return collection
        except Exception as e:
            logger.error(f"Не удалось получить коллекцию '{name}': {e}")
            return None

    @property
    def vault_notes(self):
        """Коллекция заметок Obsidian Vault."""
        return self._get_or_create(VAULT_NOTES_COLLECTION)

    @property
    def chat_messages(self):
        """Коллекция сообщений чатов GUI (заготовка)."""
        return self._get_or_create(CHAT_MESSAGES_COLLECTION)
