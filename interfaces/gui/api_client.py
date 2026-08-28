import httpx
from typing import Optional

class APIClient:
    def __init__(self, base_url: str, token: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        # Таймаут 30 секунд, чтобы GUI не зависал навечно при проблемах с ВМ
        self.client = httpx.Client(timeout=30.0)

    def set_token(self, token: str):
        self.token = token

    def _get_headers(self) -> dict:
        """Собирает заголовки для запроса, добавляя токен, если он есть."""
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def login(self, password: str) -> dict:
        response = self.client.post(f"{self.base_url}/auth/login", json={"password": password})
        response.raise_for_status()
        return response.json()

    def get_health(self) -> dict:
        response = self.client.get(f"{self.base_url}/health", headers=self._get_headers())
        response.raise_for_status()
        return response.json()


    # ============================================================================
    # Методы для работы с мультичатом (Этап 5)
    # ============================================================================

    def list_chats(self) -> list:
        """Возвращает список всех чатов."""
        response = self.client.get(f"{self.base_url}/chats", headers=self._get_headers())
        response.raise_for_status()
        return response.json()

    def create_chat(self, title: str = "") -> dict:
        """Создаёт новый чат. Возвращает {chat_id, title, created_at}."""
        data = {"title": title} if title else {}
        response = self.client.post(f"{self.base_url}/chats", json=data, headers=self._get_headers())
        response.raise_for_status()
        return response.json()

    def get_chat(self, chat_id: str) -> dict:
        """Возвращает чат с историей сообщений."""
        response = self.client.get(f"{self.base_url}/chats/{chat_id}", headers=self._get_headers())
        response.raise_for_status()
        return response.json()

    def update_chat_title(self, chat_id: str, new_title: str) -> dict:
        """Переименовывает чат."""
        response = self.client.patch(
            f"{self.base_url}/chats/{chat_id}",
            json={"title": new_title},
            headers=self._get_headers()
        )
        response.raise_for_status()
        return response.json()

    def delete_chat(self, chat_id: str) -> dict:
        """Удаляет чат."""
        response = self.client.delete(f"{self.base_url}/chats/{chat_id}", headers=self._get_headers())
        response.raise_for_status()
        return response.json()

    def send_chat_message(self, message: str, chat_id: str = "") -> dict:
        """Отправляет сообщение в чат (с опциональным chat_id)."""
        data = {"message": message}
        if chat_id:
            data["chat_id"] = chat_id
        response = self.client.post(f"{self.base_url}/chat", json=data, headers=self._get_headers())
        response.raise_for_status()
        return response.json()

    def get_agents(self) -> list:
        response = self.client.get(f"{self.base_url}/agents", headers=self._get_headers())
        response.raise_for_status()
        return response.json()

    def get_config(self) -> dict:
        response = self.client.get(f"{self.base_url}/config", headers=self._get_headers())
        response.raise_for_status()
        return response.json()

    def update_config(self, data: dict) -> dict:
        response = self.client.put(f"{self.base_url}/config", json=data, headers=self._get_headers())
        response.raise_for_status()
        return response.json()