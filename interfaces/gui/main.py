import sys
from PyQt6.QtWidgets import QApplication
from interfaces.gui.api_client import APIClient
from interfaces.gui.local_secret import load_token, clear_token
from interfaces.gui.login_window import LoginWindow
from interfaces.gui.main_window import MainWindow

# Адрес сервера по умолчанию (можно изменить в Настройках)
DEFAULT_SERVER = "http://192.168.87.205:8000"

def main():
    app = QApplication(sys.argv)
    
    # Создаём клиент с адресом по умолчанию
    api_client = APIClient(DEFAULT_SERVER)
    
    # Пробуем загрузить сохранённый токен (если ставили галочку «Запомнить»)
    token = load_token()
    if token:
        api_client.set_token(token)
        # Проверяем, действует ли токен ещё, простым запросом
        try:
            api_client.get_health()
            # Токен валиден — сразу открываем главное окно
            window = MainWindow(api_client)
            window.show()
            sys.exit(app.exec())
        except Exception:
            # Токен протух или сервер недоступен — очищаем и показываем вход
            clear_token()
            api_client.set_token("")
    
    # Показываем окно входа
    login = LoginWindow(api_client)
    if login.exec():
        # Успешный вход — открываем главное окно
        window = MainWindow(api_client)
        window.show()
        sys.exit(app.exec())
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()