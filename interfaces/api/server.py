from fastapi import FastAPI
from interfaces.api.endpoints import router
from core.logging_setup import setup_logging
from core.config import load_config
import uvicorn

def build_app() -> FastAPI:
    """
    Создаёт приложение FastAPI.
    Эта функция понадобится нам для тестов и будущих расширений.
    """
    config = load_config()
    setup_logging(config.logs_dir)  # Логи пойдут в data/logs/
    
    app = FastAPI(title="Pumka API Gateway", version="0.0.38")
    app.include_router(router)  # Подключаем все маршруты из endpoints.py
    return app

app = build_app()

if __name__ == "__main__":
    # Запуск сервера на всех сетевых интерфейсах ВМ, порт 8000
    # Это позволит подключаться к нему с вашего хоста (Windows 11)
    uvicorn.run(app, host="0.0.0.0", port=8000)
