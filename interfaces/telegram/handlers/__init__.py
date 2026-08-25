"""
interfaces/telegram/handlers/__init__.py — фабрика роутера.
Создаёт новый Router при каждом вызове и регистрирует все обработчики.
"""
from aiogram import Router
from interfaces.telegram.reactions import ReactionMiddleware
from interfaces.telegram.handlers import commands, buttons, import_content, text, media

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
    
    # Регистрируем обработчики (порядок важен!)
    # 1. Команды (/start, /help, /save и т.д.)
    commands.register(router)
    
    # 2. Кнопки главного меню
    buttons.register(router)
    
    # 3. Импорт контента (URL, фото, /save) — ДО текстовых обработчиков!
    import_content.register(router)
    
    # 4. Обычные текстовые сообщения
    text.register(router)
    
    # 5. Медиа (остальные заглушки: голосовые, видео, документы)
    media.register(router)
    
    return router
