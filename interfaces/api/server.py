from fastapi import FastAPI
from contextlib import asynccontextmanager
from interfaces.api.endpoints import router
from core.logging_setup import setup_logging
from core.config import load_config
import uvicorn
import asyncio


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan manager для FastAPI.
    Выполняет автоиндексацию Vault при старте сервера.
    """
    logger = __import__('logging').getLogger("pumka.system")
    
    # === STARTUP ===
    logger.info("Запуск сервера Pumka API...")
    
    # Автоиндексация Vault в фоне
    try:
        from services.chroma.autoindex import run_index_vault
        from services.chroma.client import ChromaClient
        
        config = load_config()
        chroma_dir = config.project_root / "data" / "chroma"
        chroma_client = ChromaClient(chroma_dir)
        
        logger.info("Запуск автоиндексации Vault в фоне...")
        
        # Запускаем в фоне, не блокируя старт сервера
        asyncio.create_task(
            run_index_vault(
                vault_root=__import__('pathlib').Path(config.paths.vault_path),
                chroma_client=chroma_client,
                max_file_size_mb=config.security.max_file_size_mb,
                background=True
            )
        )
        logger.info("Автоиндексация запущена в фоне")
    
    except Exception as e:
        # Не критично, если индексация упала — сервер продолжает работать
        logger.error(f"Ошибка при запуске автоиндексации: {e}")
        logger.warning("Сервер продолжает работу без индексации Vault")
    
    yield
    
    # === SHUTDOWN ===
    logger.info("Остановка сервера Pumka API...")


def build_app() -> FastAPI:
    """
    Создаёт приложение FastAPI.
    Эта функция понадобится нам для тестов и будущих расширений.
    """
    config = load_config()
    setup_logging(config.logs_dir)  # Логи пойдут в data/logs/
    app = FastAPI(
        title="Pumka API Gateway",
        version="0.0.38",
        lifespan=lifespan
    )
    app.include_router(router)  # Подключаем все маршруты из endpoints.py
    return app


app = build_app()

if __name__ == "__main__":
    # Запуск сервера на всех сетевых интерфейсах ВМ, порт 8000
    # Это позволит подключаться к нему с вашего хоста (Windows 11)
    uvicorn.run(app, host="0.0.0.0", port=8000)
