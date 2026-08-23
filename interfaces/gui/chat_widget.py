import httpx
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QTextEdit, QLineEdit,
                             QPushButton, QLabel)
from PyQt6.QtCore import QThread, pyqtSignal
from interfaces.gui.api_client import APIClient
from interfaces.gui.gui_log import get_gui_logger

class ChatWorker(QThread):
    """Отдельный поток, чтобы интерфейс не зависал во время ожидания ответа."""
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, api_client: APIClient, message: str):
        super().__init__()
        self.api_client = api_client
        self.message = message

    def run(self):
        log = get_gui_logger()
        try:
            response = self.api_client.send_chat_message(self.message)
            self.finished.emit(response.get("reply", "Нет ответа"))
        except httpx.ConnectError as e:
            log.error(f"Чат: сервер недоступен: {e}")
            self.error.emit("Сервер недоступен. Проверьте, что ВМ включена и API-сервер запущен.")
        except httpx.TimeoutException as e:
            log.error(f"Чат: таймаут ответа: {e}")
            self.error.emit("Сервер не ответил вовремя. Попробуйте ещё раз.")
        except httpx.HTTPStatusError as e:
            log.error(f"Чат: сервер вернул код {e.response.status_code}: {e}")
            self.error.emit(f"Сервер вернул ошибку (код {e.response.status_code}). Детали — в gui.log.")
        except Exception as e:
            log.error(f"Чат: неожиданная ошибка: {e}")
            self.error.emit("Непредвиденная ошибка. Детали — в файле gui.log.")

class ChatWidget(QWidget):
    def __init__(self, api_client: APIClient):
        super().__init__()
        self.api_client = api_client
        
        self.setStyleSheet("""
            QWidget { background-color: #1e1e1e; color: #ffffff; }
            QTextEdit { background-color: #2b2b2b; color: #ffffff; border: 1px solid #555; border-radius: 4px; }
            QLineEdit { background-color: #3c3c3c; color: white; border: 1px solid #555; padding: 6px; border-radius: 3px; }
            QPushButton { background-color: #0e639c; color: white; border: none; padding: 8px; border-radius: 4px; font-size: 13px; }
            QPushButton:hover { background-color: #1177bb; }
            QLabel { color: #aaaaaa; font-style: italic; }
        """)
        
        layout = QVBoxLayout()
        
        self.chat_area = QTextEdit()
        self.chat_area.setReadOnly(True)
        layout.addWidget(self.chat_area)
        
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)
        
        input_layout = QVBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Введите сообщение и нажмите Enter...")
        self.input_field.returnPressed.connect(self.send_message)
        input_layout.addWidget(self.input_field)
        
        self.send_btn = QPushButton("Отправить")
        self.send_btn.clicked.connect(self.send_message)
        input_layout.addWidget(self.send_btn)
        
        layout.addLayout(input_layout)
        self.setLayout(layout)

    def send_message(self):
        text = self.input_field.text().strip()
        if not text:
            return
            
        self.chat_area.append(f"<div style='text-align: right; color: #4da6ff;'><b>Вы:</b><br>{text}</div>")
        self.input_field.clear()
        self.input_field.setEnabled(False)
        self.status_label.setText("Бот печатает...")
        
        self.worker = ChatWorker(self.api_client, text)
        self.worker.finished.connect(self.on_response)
        self.worker.error.connect(self.on_error)
        self.worker.start()

    def on_response(self, reply: str):
        self.input_field.setEnabled(True)
        self.status_label.setText("")
        
        chunk_size = 4096
        for i in range(0, len(reply), chunk_size):
            chunk = reply[i:i + chunk_size]
            formatted_chunk = chunk.replace("\n", "<br>")
            self.chat_area.append(f"<div style='text-align: left; color: #ffffff;'><b>Агент:</b><br>{formatted_chunk}</div>")

    def on_error(self, error_msg: str):
        self.input_field.setEnabled(True)
        self.status_label.setText("")
        self.chat_area.append(f"<div style='text-align: left; color: #ff6666;'><b>Ошибка:</b><br>{error_msg}</div>")

    def refresh(self):
        self.chat_area.clear()
        self.chat_area.append("<div style='text-align: center; color: #888;'>История очищена (F5)</div>")