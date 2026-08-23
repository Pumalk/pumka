import httpx
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QCheckBox, QMessageBox)
from PyQt6.QtCore import Qt
from interfaces.gui.api_client import APIClient
from interfaces.gui.local_secret import save_token
from interfaces.gui.gui_log import get_gui_logger

class LoginWindow(QDialog):
    def __init__(self, api_client: APIClient):
        super().__init__()
        self.api_client = api_client
        self.setWindowTitle("Вход в Pumka")
        self.setFixedSize(300, 200)
        
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; color: #ffffff; }
            QLabel { color: #ffffff; font-size: 13px; }
            QLineEdit { background-color: #3c3c3c; color: white; border: 1px solid #555; padding: 6px; border-radius: 3px; }
            QPushButton { background-color: #0e639c; color: white; border: none; padding: 8px; border-radius: 4px; font-size: 13px; }
            QPushButton:hover { background-color: #1177bb; }
            QCheckBox { color: #ffffff; }
        """)
        
        layout = QVBoxLayout()
        
        self.label = QLabel("Введите пароль для доступа к API:")
        layout.addWidget(self.label)
        
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.returnPressed.connect(self.try_login)
        layout.addWidget(self.password_input)
        
        self.remember_check = QCheckBox("Запомнить устройство")
        layout.addWidget(self.remember_check)
        
        self.login_btn = QPushButton("Войти")
        self.login_btn.clicked.connect(self.try_login)
        layout.addWidget(self.login_btn)
        
        self.setLayout(layout)

    def try_login(self):
        """Попытка входа: отправляет пароль на сервер."""
        log = get_gui_logger()
        password = self.password_input.text()
        if not password:
            QMessageBox.warning(self, "Ошибка", "Пароль не может быть пустым")
            return
            
        self.login_btn.setEnabled(False)
        self.login_btn.setText("Проверка...")
        
        try:
            response = self.api_client.login(password)
            token = response.get("token")
            self.api_client.set_token(token)
            
            if self.remember_check.isChecked():
                save_token(token)
                
            self.accept()
        except httpx.ConnectError as e:
            log.error(f"Вход: сервер недоступен: {e}")
            QMessageBox.critical(self, "Ошибка входа",
                "Сервер недоступен.\nПроверьте, что ВМ включена и API-сервер запущен.")
        except httpx.HTTPStatusError as e:
            log.error(f"Вход: сервер вернул код {e.response.status_code}: {e}")
            if e.response.status_code == 401:
                QMessageBox.critical(self, "Ошибка входа", "Неверный пароль.")
            elif e.response.status_code == 429:
                QMessageBox.critical(self, "Ошибка входа",
                    "Слишком много неудачных попыток.\nПодождите несколько минут и попробуйте снова.")
            else:
                QMessageBox.critical(self, "Ошибка входа",
                    f"Сервер вернул ошибку (код {e.response.status_code}).\nДетали — в gui.log.")
        except Exception as e:
            log.error(f"Вход: неожиданная ошибка: {e}")
            QMessageBox.critical(self, "Ошибка входа",
                "Непредвиденная ошибка.\nДетали — в файле gui.log.")
        finally:
            self.login_btn.setEnabled(True)
            self.login_btn.setText("Войти")