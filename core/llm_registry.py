"""
core/llm_registry.py — маппинг имён моделей HuggingFace на Ollama.

Когда модель скачана через Ollama, её имя может отличаться от HF-имени.
Этот модуль содержит словарь для преобразования.
"""

import logging
from typing import Dict

logger = logging.getLogger("pumka.system")


# Словарь маппинга HF-имён на Ollama-имена
# Заполняется только теми моделями, которые уже скачаны
HF_TO_OLLAMA: Dict[str, str] = {
    # === Скачанные модели ===
    "Qwen2.5-3B-Instruct-AWQ": "huggingface.co/bartowski/Qwen2.5-3B-Instruct-GGUF:Q4_K_M",
    
    # === Заглушки (заполнятся по мере скачивания) ===
    "Qwen2.5-1.5B-Instruct": "Qwen2.5-1.5B-Instruct",
    "Qwen3-4B-Instruct-2507": "qwen3:4b",
    "Qwen2.5-Coder-3B-Instruct-AWQ": "Qwen2.5-Coder-3B-Instruct-AWQ",
    "Qwen2.5-Coder-1.5B-Instruct": "Qwen2.5-Coder-1.5B-Instruct",
    "DavidAU/Qwen3.5-9B": "DavidAU/Qwen3.5-9B",
    "nightmedia/Qwen3.5-9B-OmniCoder-Claude-Polaris": "nightmedia/Qwen3.5-9B-OmniCoder-Claude-Polaris",
    "Jackrong/Qwen3.5-9B-Claude-4.6-Opus-Reasoning": "Jackrong/Qwen3.5-9B-Claude-4.6-Opus-Reasoning",
    "khazarai/Qwen3-4B-Kimi2.5-Reasoning-Distilled": "khazarai/Qwen3-4B-Kimi2.5-Reasoning-Distilled",
    "amd/Llama-3.3-70B-Instruct-w4a16": "amd/Llama-3.3-70B-Instruct-w4a16",
    "gemma-4-E4B-it-W4A16": "gemma-4-E4B-it-W4A16",
    "Qwen3.5-9B": "Qwen3.5-9B",
    "Qwen2.5-VL-3B-Instruct-AWQ": "Qwen2.5-VL-3B-Instruct-AWQ",
    "ibm-granite/granite-embedding-311m-multilingual": "ibm-granite/granite-embedding-311m-multilingual",
    "taide/embeddinggemma-GTAIDE-300m-2605": "taide/embeddinggemma-GTAIDE-300m-2605",
}


def resolve_ollama_name(hf_name: str) -> str:
    """
    Преобразует HF-имя модели в Ollama-имя.
    
    Args:
        hf_name: Имя модели из config.yaml (HF-формат)
    
    Returns:
        Ollama-имя для использования в API запросах.
        Если маппинг не найден — возвращает hf_name как есть.
    
    Example:
        >>> resolve_ollama_name("Qwen2.5-3B-Instruct-AWQ")
        'huggingface.co/bartowski/Qwen2.5-3B-Instruct-GGUF:Q4_K_M'
        
        >>> resolve_ollama_name("UnknownModel")
        'UnknownModel'
    """
    
    ollama_name = HF_TO_OLLAMA.get(hf_name)
    
    if ollama_name is not None:
        return ollama_name
    
    # Маппинг не найден — используем как есть
    logger.warning(
        f"Маппинг для модели '{hf_name}' не найден. "
        f"Использую имя как есть. Если модель не работает — добавьте её в HF_TO_OLLAMA"
    )
    return hf_name


def get_all_ollama_names() -> list[str]:
    """Возвращает список всех Ollama-имён из словаря."""
    return list(HF_TO_OLLAMA.values())


def get_all_hf_names() -> list[str]:
    """Возвращает список всех HF-имён из словаря."""
    return list(HF_TO_OLLAMA.keys())


if __name__ == "__main__":
    # Тестовый запуск
    print("Маппинг моделей HF → Ollama:")
    print()
    for hf, ollama in HF_TO_OLLAMA.items():
        marker = "✅" if hf == "Qwen2.5-3B-Instruct-AWQ" else "⏳"
        print(f"  {marker} {hf}")
        print(f"     → {ollama}")
        print()