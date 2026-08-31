"""
interfaces/telegram/bot.py — точка входа для Telegram-бота.
Подэтап 2.3:
рефакторинг: разделение монолитного handlers.py на модули;
фабрика build_dispatcher() заменяет importlib.reload;
при падении прокси создаются НОВЫЕ Dispatcher и Router.

Этап 6: рефакторинг на create_bot_auto (санкционировано архитектором).
"""
import sys
import logging
import asyncio

from aiogram import Bot, Dispatcher

from core.config import load_config
from core.logging_setup import setup_logging
from core.tools import create_tool_registry
from interfaces.telegram.proxy import (
    create_bot_auto,
    record_failed_proxy,
    RETRY_DELAY_SECONDS,
    BotTransport,
)
from interfaces.telegram.middlewares import AccessMiddleware
from interfaces.telegram.handlers import create_router
from interfaces.telegram.memory import chat_memory

logger = logging.getLogger("pumka.system")


def build_dispatcher(config, tool_registry) -> Dispatcher:
    """
    Создаёт новый Dispatcher с новым Router.
    Вызывается при каждом запуске/перезапуске бота (например, при падении прокси).
    Благодаря этому не нужен importlib.reload и нет ошибки
    "Router is already attached".
    """
    dp = Dispatcher()
    access_middleware = AccessMiddleware()
    dp.message.outer_middleware(access_middleware)
    dp.callback_query.outer_middleware(access_middleware)
    dp.include_router(create_router())
    dp["config"] = config
    dp["tool_registry"] = tool_registry
    dp["chat_memory"] = chat_memory
    return dp


async def run_bot_with_transport(
    transport: BotTransport, config, tool_registry
) -> None:
    """Запускает бота с выбранным транспортом."""
    dp = build_dispatcher(config, tool_registry)
    if transport.mode == "proxy":
        logger.info(f"Бот готов к работе с прокси: {transport.proxy}")
    elif transport.mode == "worker":
        logger.info("Бот готов к работе через Worker")
    else:
        logger.info("Бот готов к работе без прокси")
    print("✅ Бот готов к работе. Ожидание сообщений...")
    try:
        await dp.start_polling(transport.bot, timeout=70)
    finally:
        await transport.bot.session.close()


async def main():
    """Главная функция запуска бота."""
    config = load_config()
    setup_logging(config.logs_dir)

    if not config.telegram.token:
        print("[ОШИБКА] Telegram токен не задан в .env")
        print("        Добавьте переменную TELEGRAM_BOT_TOKEN в файл .env")
        sys.exit(1)

    if config.telegram.allowed_user_id is None:
        print("⚠️ Доступ не ограничен: TELEGRAM_ALLOWED_USER_ID не задан.")
        print("   Безопасность важнее удобства.")
        print("   Добавьте переменную TELEGRAM_ALLOWED_USER_ID в файл .env")
        sys.exit(1)

    logger.info("Запуск Telegram-бота Pumka")
    logger.info(f"Разрешённый пользователь: {config.telegram.allowed_user_id}")

    tool_registry = create_tool_registry(config.security.allowed_paths)
    logger.info(
        f"Реестр инструментов создан: {len(tool_registry.list_tools())} инструментов"
    )

    # ------------------------------------------------------------------
    # Режим автоподбора прокси: цикл с повторными попытками
    # ------------------------------------------------------------------
    if config.telegram.proxy_auto:
        print("🔄 Режим автоподбора прокси включён")

        while True:
            transport = await create_bot_auto(config)

            if transport is None:
                print(
                    "[ОШИБКА] После повторной проверки рабочих прокси нет. Бот остановлен."
                )
                sys.exit(1)

            if transport.mode == "proxy":
                print(f"🚀 Запуск бота с прокси {transport.proxy}")

            try:
                await run_bot_with_transport(transport, config, tool_registry)
                break  # polling завершился штатно (Ctrl+C)
            except Exception as e:
                # Невалидный токен — повторные проверки бесполезны
                if "unauthorized" in str(e).lower():
                    logger.error(f"Невалидный токен бота: {e}")
                    print(
                        "[ОШИБКА] Telegram не принял токен. Проверьте TELEGRAM_BOT_TOKEN."
                    )
                    sys.exit(1)

                if transport.mode == "proxy" and transport.proxy:
                    record_failed_proxy(transport.proxy)
                    logger.error(f"Ошибка с прокси {transport.proxy}: {e}")
                    print(
                        f"❌ Прокси {transport.proxy} упал. Помечен как нестабильный. Повторное тестирование..."
                    )
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                else:
                    # Ошибка не связана с прокси (например, прямой доступ упал)
                    logger.error(f"Ошибка при работе бота: {e}")
                    print(f"[ОШИБКА] Ошибка при работе бота: {e}")
                    sys.exit(1)

    # ------------------------------------------------------------------
    # Без цикла повторных попыток: Worker, ручной прокси или прямой доступ
    # ------------------------------------------------------------------
    else:
        transport = await create_bot_auto(config)

        if transport is None:
            print("[ОШИБКА] Не удалось создать транспорт для бота")
            sys.exit(1)

        if transport.mode == "proxy":
            print(f"🔗 Прокси из .env: {transport.proxy}")
        elif transport.mode == "direct":
            print("🔗 Прокси не задан")

        try:
            await run_bot_with_transport(transport, config, tool_registry)
        except Exception as e:
            logger.error(f"Ошибка при работе бота: {e}")
            print(f"[ОШИБКА] Ошибка при работе бота: {e}")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
