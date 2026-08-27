"""
interfaces/telegram/bot.py — точка входа для Telegram-бота.
Подэтап 2.3:
рефакторинг: разделение монолитного handlers.py на модули;
фабрика build_dispatcher() заменяет importlib.reload;
при падении прокси создаются НОВЫЕ Dispatcher и Router.
"""
import sys
import logging
import asyncio

from aiogram import Bot, Dispatcher
from core.config import load_config
from core.logging_setup import setup_logging
from core.tools import create_tool_registry

from interfaces.telegram.proxy import (
    normalize_proxy,
    load_proxies_from_api,
    scan_all_proxies,
    create_bot_with_proxy,
    create_bot_with_worker,
    FAILED_PROXIES_FILE,
    failed_proxies,
    RETRY_DELAY_SECONDS,
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


async def run_bot_with_proxy(token: str, proxy: str, config, tool_registry) -> None:
    """Запускает бота с указанным прокси."""
    bot = create_bot_with_proxy(token=token, proxy=proxy)
    dp = build_dispatcher(config, tool_registry)
    logger.info(f"Бот готов к работе с прокси: {proxy}")
    print("✅ Бот готов к работе. Ожидание сообщений...")
    try:
        await dp.start_polling(bot, timeout=70)
    finally:
        await bot.session.close()

async def test_worker_connection(bot: Bot, timeout: int = 10) -> bool:
    """
    Проверяет доступность Worker через getMe с таймаутом.
    Возвращает True, если Worker отвечает корректно.
    """
    try:
        me = await bot.get_me(request_timeout=timeout)
        logger.info(f"Worker доступен: бот @{me.username} (ID: {me.id})")
        return True
    except Exception as e:
        logger.warning(f"Worker недоступен: {e}")
        return False

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
    
        # --------------------------------------------------------------
    # Cloudflare Worker: основной транспорт (если задан)
    # --------------------------------------------------------------
    if config.telegram.telegram_api_url and config.telegram.telegram_proxy_key:
        logger.info(f"Попытка запуска через Worker: {config.telegram.telegram_api_url}")
        print(f"🌐 Попытка запуска через Cloudflare Worker...")
        
        bot = create_bot_with_worker(
            token=config.telegram.token,
            api_url=config.telegram.telegram_api_url,
            proxy_key=config.telegram.telegram_proxy_key,
        )
        
        if await test_worker_connection(bot):
            print("✅ Worker доступен, запуск бота через Worker")
            dp = build_dispatcher(config, tool_registry)
            try:
                await dp.start_polling(bot, timeout=70)
            finally:
                await bot.session.close()
            return
        else:
            logger.warning("Worker недоступен, переключаюсь на автоподбор прокси")
            print("⚠️ Worker недоступен, переключаюсь на автоподбор прокси")
            await bot.session.close()
            # Продолжаем выполнение — падаем в блок proxy_auto или proxy
    
    # --------------------------------------------------------------
    # Режим автоподбора прокси: проверка ВСЕХ и выбор самого быстрого
    # --------------------------------------------------------------
    if config.telegram.proxy_auto:
        logger.info("Режим автоподбора прокси включён")
        print("🔄 Режим автоподбора прокси включён")
        proxies = await load_proxies_from_api(
            config.telegram.proxy_api_url,
            use_doh=config.telegram.proxy_use_doh,
            doh_url=config.telegram.proxy_doh_url,
        )
        if not proxies:
            print("[ОШИБКА] Не удалось загрузить список прокси из API")
            sys.exit(1)
        working = await scan_all_proxies(
            proxies, select_best=config.telegram.proxy_select_best
        )
        if not working:
            print("[ОШИБКА] Не найдено рабочих прокси")
            sys.exit(1)
        # Основной цикл: при падении прокси — повторная проверка всех
        while working:
            proxy, latency = working.pop(0)
            print(f"🚀 Запуск бота с прокси {proxy} ({latency * 1000:.0f} мс)")
            try:
                await run_bot_with_proxy(
                    token=config.telegram.token,
                    proxy=proxy,
                    config=config,
                    tool_registry=tool_registry,
                )
                break  # polling завершился штатно (Ctrl+C)
            except Exception as e:
                # Невалидный токен — повторные проверки бесполезны
                if "unauthorized" in str(e).lower():
                    logger.error(f"Невалидный токен бота: {e}")
                    print(
                        "[ОШИБКА] Telegram не принял токен. Проверьте TELEGRAM_BOT_TOKEN."
                    )
                    sys.exit(1)
                # Помечаем прокси как упавший, чтобы не пробовать его снова
                failed_proxies.add(proxy)
                # Сохраняем в файл
                try:
                    FAILED_PROXIES_FILE.parent.mkdir(parents=True, exist_ok=True)
                    with open(FAILED_PROXIES_FILE, "a") as f:
                        f.write(f"{proxy}\n")
                except Exception as e:
                    logger.warning(f"Не удалось сохранить упавший прокси в файл: {e}")
                logger.error(f"Ошибка с прокси {proxy}: {e}")
                print(
                    f"❌ Прокси {proxy} упал. Помечен как нестабильный. Повторное тестирование..."
                )
                await asyncio.sleep(RETRY_DELAY_SECONDS)
                proxies = await load_proxies_from_api(
                    config.telegram.proxy_api_url,
                    use_doh=config.telegram.proxy_use_doh,
                    doh_url=config.telegram.proxy_doh_url,
                )
                if not proxies:
                    print("[ОШИБКА] Не удалось перезагрузить список прокси")
                    sys.exit(1)
                working = await scan_all_proxies(
                    proxies, select_best=config.telegram.proxy_select_best
                )
                if not working:
                    print(
                        "[ОШИБКА] После повторной проверки рабочих прокси нет. Бот остановлен."
                    )
                    sys.exit(1)
    # --------------------------------------------------------------
    # Ручной прокси из .env
    # --------------------------------------------------------------
    elif config.telegram.proxy:
        proxy = normalize_proxy(config.telegram.proxy)
        print(f"🔗 Прокси из .env: {proxy}")
        try:
            await run_bot_with_proxy(
                token=config.telegram.token,
                proxy=proxy,
                config=config,
                tool_registry=tool_registry,
            )
        except Exception as e:
            logger.error(f"Ошибка при работе бота: {e}")
            print(f"[ОШИБКА] Ошибка при работе бота: {e}")
            sys.exit(1)
    # --------------------------------------------------------------
    # Без прокси
    # --------------------------------------------------------------
    else:
        print("🔗 Прокси не задан")
        try:
            await run_bot_with_proxy(
                token=config.telegram.token,
                proxy="",
                config=config,
                tool_registry=tool_registry,
            )
        except Exception as e:
            logger.error(f"Ошибка при работе бота: {e}")
            print(f"[ОШИБКА] Ошибка при работе бота: {e}")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
