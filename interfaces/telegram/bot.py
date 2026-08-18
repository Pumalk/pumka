"""
interfaces/telegram/bot.py — точка входа для Telegram-бота.
Проверяет ВСЕ прокси из списка параллельно, замеряет скорость
и выбирает самый быстрый. При падении прокси — повторная проверка.
Сохраняет упавшие прокси в файл, чтобы не проверять их снова.
Опционально использует DNS-over-HTTPS (DoH) для загрузки списка.
"""

import sys
import socket
import logging
import asyncio
import time
from pathlib import Path
from typing import List, Optional, Tuple

import aiohttp
from aiohttp.abc import AbstractResolver
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from core.config import load_config
from core.logging_setup import setup_logging
from core.tools import create_tool_registry

from interfaces.telegram.handlers import router, AccessMiddleware


logger = logging.getLogger("pumka.system")

# Максимум одновременных проверок прокси
MAX_CONCURRENT_CHECKS = 50

# Предохранитель: не проверять больше этого числа
MAX_PROXIES_TOTAL = 500

# Таймаут проверки одного прокси (секунды)
PROXY_CHECK_TIMEOUT = 5

# Пауза перед повторной проверкой после падения (секунды)
RETRY_DELAY_SECONDS = 5

# Путь к файлу для хранения упавших прокси
FAILED_PROXIES_FILE = Path(__file__).parent.parent.parent / "data" / "temp" / "failed_proxies.txt"

# Множество для отслеживания упавших прокси (исключаем их из повторных проверок)
failed_proxies: set = set()


# ============================================================================
# DNS-over-HTTPS (DoH) — защита от подмены DNS при загрузке списка прокси
# ============================================================================

class DohResolver(AbstractResolver):
    """
    Резолвер доменных имён через DNS-over-HTTPS.
    Если DoH не ответил — откатывается на системный DNS.
    Нужен, чтобы подмена DNS провайдером не мешала загрузке списка прокси.
    """

    def __init__(self, doh_url: str):
        self.doh_url = doh_url

    async def _resolve_via_doh(self, host: str) -> Optional[str]:
        """Спрашивает у DoH-сервера IP для домена. Возвращает IP или None."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.doh_url,
                    params={"name": host, "type": "A"},
                    headers={"Accept": "application/dns-json"},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as response:
                    if response.status != 200:
                        return None
                    data = await response.json()
                    for answer in data.get("Answer", []):
                        if answer.get("type") == 1:  # A-запись
                            return answer.get("data")
        except Exception as e:
            logger.debug(f"DoH не смог разрешить {host}: {e}")
        return None

    async def _getaddrinfo(self, host: str, port: int) -> List[dict]:
        """Стандартное разрешение имени в структуру, понятную aiohttp."""
        loop = asyncio.get_event_loop()
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        result = []
        for family, type_, proto, _canon, sockaddr in infos:
            result.append({
                "family": family,
                "type": type_,
                "proto": proto,
                "flags": socket.AI_NUMERICHOST,
                "host": sockaddr[0],
                "port": sockaddr[1],
                "hostname": host,
            })
        return result

    async def resolve(self, host: str, port: int = 0, family: int = 0) -> List[dict]:
        """Разрешает домен: сначала DoH, при неудаче — системный DNS."""
        port = port or 443
        ip = await self._resolve_via_doh(host)
        if ip:
            logger.debug(f"DoH: {host} -> {ip}")
            return await self._getaddrinfo(ip, port)
        return await self._getaddrinfo(host, port)

    async def close(self) -> None:
        """Обязательный метод интерфейса aiohttp. Закрывать нечего."""
        pass


# ============================================================================
# Работа со списком прокси
# ============================================================================

def normalize_proxy(proxy: str) -> str:
    """Добавляет префикс socks5://, если его нет."""
    proxy = proxy.strip()
    if not proxy:
        return ""
    if not proxy.startswith(("http://", "https://", "socks5://", "socks4://")):
        proxy = f"socks5://{proxy}"
    return proxy


async def load_proxies_from_api(
    api_url: str,
    use_doh: bool = False,
    doh_url: str = "",
) -> List[str]:
    """
    Загружает список прокси из API.
    Если use_doh=True — домен API разрешается через DoH.
    """
    logger.info(f"Загрузка списка прокси из API: {api_url}")
    if use_doh and doh_url:
        logger.info(f"Используется DoH: {doh_url}")

    connector = None
    if use_doh and doh_url:
        connector = aiohttp.TCPConnector(resolver=DohResolver(doh_url))

    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                api_url,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status != 200:
                    logger.error(f"API вернул статус {response.status}")
                    return []
                text = await response.text()
                proxies = [
                    line.strip()
                    for line in text.strip().split("\n")
                    if line.strip() and ":" in line
                ]
                logger.info(f"Загружено {len(proxies)} прокси из API")
                return proxies
    except Exception as e:
        logger.error(f"Ошибка при загрузке прокси из API: {e}")
        return []


async def check_proxy_with_latency(
    proxy_url: str,
    semaphore: asyncio.Semaphore,
    timeout: int = PROXY_CHECK_TIMEOUT,
) -> Optional[Tuple[str, float]]:
    """
    Проверяет прокси и замеряет время отклика api.telegram.org через него.
    Возвращает (прокси, задержка в секундах) или None, если прокси не работает.
    """
    async with semaphore:
        start = time.monotonic()
        try:
            if proxy_url.startswith(("socks5://", "socks4://")):
                try:
                    from aiohttp_socks import ProxyConnector
                    connector = ProxyConnector.from_url(proxy_url)
                except ImportError:
                    return None
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.get(
                        "https://api.telegram.org",
                        timeout=aiohttp.ClientTimeout(total=timeout)
                    ) as response:
                        ok = response.status == 200
            else:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        "https://api.telegram.org",
                        proxy=proxy_url,
                        timeout=aiohttp.ClientTimeout(total=timeout)
                    ) as response:
                        ok = response.status == 200
        except Exception:
            ok = False
        latency = time.monotonic() - start
        return (proxy_url, latency) if ok else None


async def scan_all_proxies(proxies: List[str], select_best: bool = True) -> List[Tuple[str, float]]:
    """
    Проверяет ВСЕ прокси параллельно (до 50 одновременно).
    Возвращает рабочие прокси с замеренной задержкой.
    Если select_best=True — сортирует от самого быстрого к медленному.
    Исключает прокси, которые уже падали ранее.
    """
    # Загружаем упавшие прокси из файла
    if FAILED_PROXIES_FILE.exists():
        try:
            with open(FAILED_PROXIES_FILE, 'r') as f:
                failed_from_file = set(line.strip() for line in f if line.strip())
                failed_proxies.update(failed_from_file)
        except Exception as e:
            logger.warning(f"Не удалось загрузить список упавших прокси: {e}")

    # Исключаем прокси, которые уже падали
    proxies = [p for p in proxies if normalize_proxy(p) not in failed_proxies]
    proxies = proxies[:MAX_PROXIES_TOTAL]
    normalized = [normalize_proxy(p) for p in proxies]

    print(f"🔍 Проверка {len(normalized)} прокси параллельно...")
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)

    results = await asyncio.gather(
        *[check_proxy_with_latency(p, semaphore) for p in normalized]
    )

    working = [r for r in results if r is not None]
    if select_best:
        working.sort(key=lambda item: item[1])

    print(f"✅ Рабочих прокси: {len(working)} из {len(normalized)}")
    for proxy, latency in working[:5]:
        print(f"   ⚡ {proxy} — {latency * 1000:.0f} мс")

    logger.info(f"Сканирование прокси: {len(working)} рабочих из {len(normalized)}")
    return working


# ============================================================================
# Создание и запуск бота
# ============================================================================

def create_bot_with_proxy(token: str, proxy: str = "") -> Bot:
    """Создаёт бота с поддержкой прокси (HTTP или SOCKS5)."""
    if proxy:
        from aiogram.client.session.aiohttp import AiohttpSession

        if proxy.startswith("socks"):
            try:
                import aiohttp_socks  # noqa: F401
            except ImportError:
                print("[ОШИБКА] Для SOCKS5-прокси нужна библиотека aiohttp_socks")
                print("        Установите: pip install aiohttp_socks")
                sys.exit(1)

        session = AiohttpSession(proxy=proxy)
        logger.info(f"Бот создан с прокси: {proxy}")
        return Bot(
            token=token,
            session=session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )

    logger.info("Бот создан без прокси")
    return Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )


async def run_bot_with_proxy(token: str, proxy: str, config, tool_registry) -> None:
    """Запускает бота с указанным прокси."""
    bot = create_bot_with_proxy(token=token, proxy=proxy)

    dp = Dispatcher()

    access_middleware = AccessMiddleware()
    dp.message.outer_middleware(access_middleware)
    dp.callback_query.outer_middleware(access_middleware)

    dp.include_router(router)

    dp["config"] = config
    dp["tool_registry"] = tool_registry

    logger.info(f"Бот готов к работе с прокси: {proxy}")
    print("✅ Бот готов к работе. Ожидание сообщений...")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


# ============================================================================
# Главная функция
# ============================================================================

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
    logger.info(f"Реестр инструментов создан: {len(tool_registry.list_tools())} инструментов")

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

        working = await scan_all_proxies(proxies, select_best=config.telegram.proxy_select_best)
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
                    print("[ОШИБКА] Telegram не принял токен. Проверьте TELEGRAM_BOT_TOKEN.")
                    sys.exit(1)

                # Помечаем прокси как упавший, чтобы не пробовать его снова
                failed_proxies.add(proxy)

                # Сохраняем в файл
                try:
                    FAILED_PROXIES_FILE.parent.mkdir(parents=True, exist_ok=True)
                    with open(FAILED_PROXIES_FILE, 'a') as f:
                        f.write(f"{proxy}\n")
                except Exception as e:
                    logger.warning(f"Не удалось сохранить упавший прокси в файл: {e}")

                logger.error(f"Ошибка с прокси {proxy}: {e}")
                print(f"❌ Прокси {proxy} упал. Помечен как нестабильный. Повторное тестирование...")
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
                    proxies,
                    select_best=config.telegram.proxy_select_best
                )
                if not working:
                    print("[ОШИБКА] После повторной проверки рабочих прокси нет. Бот остановлен.")
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