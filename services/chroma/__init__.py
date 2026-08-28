"""
services/chroma — работа с ChromaDB (векторное хранилище).
Этап 5: индексация заметок Vault + заготовка для истории чатов.
"""
from services.chroma.client import ChromaClient

__all__ = ["ChromaClient"]
