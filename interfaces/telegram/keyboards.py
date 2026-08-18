"""
interfaces/telegram/keyboards.py — Reply-клавиатуры для Telegram-бота.
Кнопки отображаются под полем ввода как быстрые команды.
"""

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """
    Создаёт reply-клавиатуру главного меню.
    Кнопки отображаются под полем ввода.
    
    Returns:
        ReplyKeyboardMarkup с 4 кнопками
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💬 Чат")],
            [KeyboardButton(text="📦 Очередь")],
            [KeyboardButton(text="🏥 Здоровье")],
            [KeyboardButton(text="ℹ️ Помощь")]
        ],
        resize_keyboard=True,
        is_persistent=True  # клавиатура сохраняется между перезапусками
    )