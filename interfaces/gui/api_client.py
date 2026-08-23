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

    def send_chat_message(self, message: str) -> dict:
        response = self.client.post(f"{self.base_url}/chat", json={"message": message}, headers=self._get_headers())
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