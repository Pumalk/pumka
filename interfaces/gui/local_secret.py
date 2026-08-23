import os
from pathlib import Path
from cryptography.fernet import Fernet

# Папка для хранения секретов в Windows (скрытая, системная)
APP_DIR = Path(os.environ.get("APPDATA", "")) / "Pumka"
APP_DIR.mkdir(parents=True, exist_ok=True)

KEY_FILE = APP_DIR / "key.key"
TOKEN_FILE = APP_DIR / "token.enc"

def get_fernet() -> Fernet:
    """Возвращает или создаёт ключ шифрования один раз."""
    if not KEY_FILE.exists():
        key = Fernet.generate_key()
        KEY_FILE.write_bytes(key)
    return Fernet(KEY_FILE.read_bytes())

def save_token(token: str):
    """Шифрует и сохраняет токен, чтобы не вводить пароль каждый раз."""
    f = get_fernet()
    encrypted = f.encrypt(token.encode())
    TOKEN_FILE.write_bytes(encrypted)

def load_token() -> str | None:
    """Расшифровывает токен при запуске программы."""
    if not TOKEN_FILE.exists():
        return None
    try:
        f = get_fernet()
        encrypted = TOKEN_FILE.read_bytes()
        return f.decrypt(encrypted).decode()
    except Exception:
        # Если ключ сломался или токен повредился, удаляем его
        TOKEN_FILE.unlink(missing_ok=True)
        return None

def clear_token():
    """Удаляет сохранённый токен (при выходе из аккаунта)."""
    TOKEN_FILE.unlink(missing_ok=True)