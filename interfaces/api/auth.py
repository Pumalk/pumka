import time
import jwt
import logging
from fastapi import HTTPException, Request
from core.config import load_config

logger = logging.getLogger("pumka.system")
incidents_logger = logging.getLogger("pumka.incidents")

# Хранилище попыток входа в памяти
login_attempts = {}

def check_bruteforce(client_ip: str):
    """Проверяет, не заблокирован ли IP из-за частых ошибок."""
    now = time.time()
    if client_ip in login_attempts:
        data = login_attempts[client_ip]
        if data["blocked_until"] > now:
            remaining = int(data["blocked_until"] - now)
            incidents_logger.warning(f"Брутфорс: блокировка IP {client_ip}, осталось {remaining} сек")
            raise HTTPException(status_code=429, detail=f"Слишком много попыток. Подождите {remaining} сек.")
        elif now - data["last_attempt"] > 300:
            login_attempts[client_ip] = {"count": 0, "blocked_until": 0, "last_attempt": now}

def record_failed_attempt(client_ip: str):
    """Записывает неудачную попытку и блокирует при 5 ошибках."""
    now = time.time()
    if client_ip not in login_attempts:
        login_attempts[client_ip] = {"count": 0, "blocked_until": 0, "last_attempt": now}
    
    data = login_attempts[client_ip]
    data["count"] += 1
    data["last_attempt"] = now
    
    if data["count"] >= 5:
        data["blocked_until"] = now + 300
        incidents_logger.error(f"БРУТФОРС: IP {client_ip} заблокирован на 5 минут после 5 неудачных попыток.")

def reset_attempts(client_ip: str):
    """Сбрасывает счётчик при успешном входе."""
    if client_ip in login_attempts:
        del login_attempts[client_ip]

def create_jwt_token() -> str:
    """Создаёт JWT-токен сроком на 7 дней."""
    config = load_config()
    secret = config.gui.device_token_key
    if not secret:
        raise HTTPException(status_code=500, detail="DEVICE_TOKEN_KEY не настроен на сервере")
    
    expire = time.time() + (7 * 24 * 60 * 60)
    payload = {"exp": expire, "sub": "pumka_gui_user"}
    return jwt.encode(payload, secret, algorithm="HS256")

def verify_jwt_token(request: Request) -> bool:
    """Проверяет валидность JWT-токена из заголовка."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Отсутствует или неверный заголовок Authorization")
    
    token = auth_header.split(" ")[1]
    config = load_config()
    secret = config.gui.device_token_key
    
    try:
        jwt.decode(token, secret, algorithms=["HS256"])
        return True
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Срок действия токена истёк")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Неверный токен")
