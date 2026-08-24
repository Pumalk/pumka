"""
interfaces/telegram/keyboards.py — Reply-клавиатуры для Telegram-бота.
Кнопки отображаются под полем ввода как быстрые команды.
"""

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """
    Создаёт reply-клавиатуру главного меню.
    Раскладка 2x2 для читаемости на телефоне и ПК.

    Returns:
        ReplyKeyboardMarkup с 4 кнопками в раскладке 2x2
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🆕 Новый чат"),
                KeyboardButton(text="📦 Очередь"),
            ],
            [
                KeyboardButton(text="🏥 Здоровье"),
                KeyboardButton(text="🔽 Скрыть"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=False,  # клавиатура НЕ сохраняется, клиент может скрывать
    )
