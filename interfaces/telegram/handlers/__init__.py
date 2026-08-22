"""
interfaces/telegram/handlers/__init__.py — фабрика роутера.
Создаёт новый Router при каждом вызове и регистрирует все обработчики.
"""
from aiogram import Router
from interfaces.telegram.reactions import ReactionMiddleware
from interfaces.telegram.handlers import commands, buttons, text, media


def create_router() -> Router:
    """
    Создаёт новый Router с зарегистрированными обработчиками.
    Вызывается из bot.py при каждом создании Dispatcher.
    Каждый вызов создаёт НОВЫЕ объекты Router, что решает проблему
    "Router is already attached" при перезапуске на новом прокси.
    """
    router = Router()
    # Регистрируем ReactionMiddleware
    router.message.outer_middleware(ReactionMiddleware())
    # Регистрируем обработчики (порядок важен: команды → кнопки → текст → медиа)
    commands.register(router)
    buttons.register(router)
    text.register(router)
    media.register(router)
    return router
