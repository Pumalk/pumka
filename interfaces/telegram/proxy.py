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
from typing import List, Optional, Tuple

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