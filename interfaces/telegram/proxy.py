"""
interfaces/telegram/proxy.py — автоподбор прокси, DoH, переключение при падении.
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
from typing import List, Optional, Tuple, NamedTuple

import aiohttp
from aiohttp.abc import AbstractResolver
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiohttp import ClientSession, ClientTimeout
from aiohttp.hdrs import USER_AGENT

logger = logging.getLogger("pumka.system")

# Максимум одновременных проверок прокси
MAX_CONCURRENT_CHECKS = 50
# Предохранитель: не проверять больше этого числа
MAX_PROXIES_TOTAL = 500
# Таймаут проверки одного прокси (секунды)
PROXY_CHECK_TIMEOUT = 10
# Пауза перед повторной проверкой после падения (секунды)
RETRY_DELAY_SECONDS = 5
# Путь к файлу для хранения упавших прокси
FAILED_PROXIES_FILE = (
    Path(__file__).parent.parent.parent / "data" / "temp" / "failed_proxies.txt"
)
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
            result.append(
                {
                    "family": family,
                    "type": type_,
                    "proto": proto,
                    "flags": socket.AI_NUMERICHOST,
                    "host": sockaddr[0],
                    "port": sockaddr[1],
                    "hostname": host,
                }
            )
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
    connector = None
    if use_doh and doh_url:
        logger.info(f"Используется DoH: {doh_url}")
        connector = aiohttp.TCPConnector(resolver=DohResolver(doh_url))
    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                api_url, timeout=aiohttp.ClientTimeout(total=15)
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
                        timeout=aiohttp.ClientTimeout(total=timeout),
                    ) as response:
                        ok = response.status == 200
            else:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        "https://api.telegram.org",
                        proxy=proxy_url,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                    ) as response:
                        ok = response.status == 200
        except Exception:
            ok = False
        latency = time.monotonic() - start
        return (proxy_url, latency) if ok else None


async def scan_all_proxies(
    proxies: List[str], select_best: bool = True
) -> List[Tuple[str, float]]:
    """
    Проверяет ВСЕ прокси параллельно (до 50 одновременно).
    Возвращает рабочие прокси с замеренной задержкой.
    Если select_best=True — сортирует от самого быстрого к медленному.
    Исключает прокси, которые уже падали ранее.
    """
    # Загружаем упавшие прокси из файла
    if FAILED_PROXIES_FILE.exists():
        try:
            with open(FAILED_PROXIES_FILE, "r") as f:
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

class ProxyKeySession(AiohttpSession):
    """
    Наследник AiohttpSession, добавляющий заголовок X-Proxy-Key во все запросы.
    Нужен для работы через Cloudflare Worker: Worker проверяет этот заголовок
    и без него возвращает 403. Заголовок добавляется и для API-методов, и для
    скачивания файлов (/file/bot...), что критично для отправки фото/документов.
    
    Увеличенный таймаут (70 сек) нужен для Telegram long-polling (getUpdates),
    который висит до 30 секунд в ожидании новых сообщений.
    """

    def __init__(self, proxy_key: str, **kwargs):
        # Устанавливаем таймаут сессии ДО вызова parent __init__
        kwargs.setdefault('timeout', 70.0)
        super().__init__(**kwargs)
        self.proxy_key = proxy_key

    async def create_session(self) -> ClientSession:
        """Создаёт ClientSession с заголовком X-Proxy-Key по умолчанию."""
        if self._should_reset_connector:
            await self.close()
        if self._session is None or self._session.closed:
            self._session = ClientSession(
                connector=self._connector_type(**self._connector_init),
                headers={
                    USER_AGENT: f"aiogram/{__version__ if '__version__' in globals() else '3.13.1'}",
                    "X-Proxy-Key": self.proxy_key,
                },
                timeout=ClientTimeout(total=self.timeout),
            )
            self._should_reset_connector = False
        return self._session

# ============================================================================
# Создание бота
# ============================================================================
def create_bot_with_proxy(token: str, proxy: str = "") -> Bot:
    """Создаёт бота с поддержкой прокси (HTTP или SOCKS5)."""
    if proxy:
        if proxy.startswith("socks"):
            try:
                import aiohttp_socks  # noqa: F401
            except ImportError:
                print("[ОШИБКА] Для SOCKS5-прокси нужна библиотека aiohttp_socks")
                print("        Установите: pip install aiohttp_socks")
                sys.exit(1)
        # Используем дефолтный таймаут aiogram (не задаём свой)
        session = AiohttpSession(proxy=proxy)
        logger.info(f"Бот создан с прокси: {proxy}")
        return Bot(
            token=token,
            session=session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    logger.info("Бот создан без прокси")
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

def create_bot_with_worker(token: str, api_url: str, proxy_key: str) -> Bot:
    """
    Создаёт бота с использованием Cloudflare Worker как транспорта.
    Worker проверяет заголовок X-Proxy-Key (через ProxyKeySession).
    Без прокси — Worker сам проксирует запросы на api.telegram.org.
    """
    api_server = TelegramAPIServer.from_base(api_url)
    session = ProxyKeySession(proxy_key=proxy_key, api=api_server)
    logger.info(f"Бот создан с Worker: {api_url}")
    return Bot(
        token=token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

# ============================================================================
# Автоматический выбор транспорта для бота и воркеров (Этап 6)
# ============================================================================

class BotTransport(NamedTuple):
    """Результат автоматического выбора транспорта для бота."""
    bot: Bot
    mode: str              # "worker" | "proxy" | "direct"
    proxy: Optional[str]   # URL прокси, если mode == "proxy", иначе None


def record_failed_proxy(proxy: str) -> None:
    """
    Помечает прокси как упавший и сохраняет в файл.
    Общая функция для bot.py и воркеров (санкционированный рефакторинг).
    """
    failed_proxies.add(proxy)
    try:
        FAILED_PROXIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(FAILED_PROXIES_FILE, "a") as f:
            f.write(f"{proxy}\n")
    except Exception as e:
        logger.warning(f"Не удалось сохранить упавший прокси в файл: {e}")


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


async def create_bot_auto(config) -> Optional[BotTransport]:
    """
    Создаёт бота, автоматически выбирая лучший транспорт (один раз):
    1. Cloudflare Worker (если задан и доступен)
    2. Автоподбор SOCKS5 прокси (если proxy_auto=True)
    3. Ручной прокси из .env
    4. Без прокси
    
    Используется как в основном цикле бота, так и в воркерах RQ.
    Возвращает BotTransport или None (если proxy_auto включён, но рабочих прокси нет).
    """
    # 1. Worker
    if config.telegram.telegram_api_url and config.telegram.telegram_proxy_key:
        logger.info(f"Попытка создания бота через Worker: {config.telegram.telegram_api_url}")
        print("🌐 Попытка запуска через Cloudflare Worker...")
        bot = create_bot_with_worker(
            token=config.telegram.token,
            api_url=config.telegram.telegram_api_url,
            proxy_key=config.telegram.telegram_proxy_key,
        )
        if await test_worker_connection(bot):
            logger.info("Worker доступен, бот создан через Worker")
            print("✅ Worker доступен")
            return BotTransport(bot=bot, mode="worker", proxy=None)
        await bot.session.close()
        logger.warning("Worker недоступен, переключаюсь на прокси")
        print("⚠️ Worker недоступен, переключаюсь на прокси")

    # 2. Автоподбор прокси
    if config.telegram.proxy_auto:
        logger.info("Режим автоподбора прокси включён")
        proxies = await load_proxies_from_api(
            config.telegram.proxy_api_url,
            use_doh=config.telegram.proxy_use_doh,
            doh_url=config.telegram.proxy_doh_url,
        )
        if proxies:
            working = await scan_all_proxies(
                proxies, select_best=config.telegram.proxy_select_best
            )
            if working:
                proxy, latency = working[0]
                logger.info(f"Выбран лучший прокси: {proxy} ({latency * 1000:.0f} мс)")
                bot = create_bot_with_proxy(token=config.telegram.token, proxy=proxy)
                return BotTransport(bot=bot, mode="proxy", proxy=proxy)
        # proxy_auto включён, но рабочих прокси нет
        logger.error("Не найдено рабочих прокси при автоподборе")
        return None

    # 3. Ручной прокси
    if config.telegram.proxy:
        proxy = normalize_proxy(config.telegram.proxy)
        logger.info(f"Используется прокси из .env: {proxy}")
        bot = create_bot_with_proxy(token=config.telegram.token, proxy=proxy)
        return BotTransport(bot=bot, mode="proxy", proxy=proxy)

    # 4. Без прокси
    logger.info("Бот создан без прокси")
    bot = create_bot_with_proxy(token=config.telegram.token, proxy="")
    return BotTransport(bot=bot, mode="direct", proxy=None)
