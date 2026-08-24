"""
interfaces/gui/chat_widget.py — виджет чата.
Добавлен режим чтения: кнопка «📖 Чтение» в шапке + плавающая «✕» в углу.
"""

import httpx
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTextEdit,
    QLineEdit,
    QPushButton,
    QLabel,
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt
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
            self.error.emit(
                "Сервер недоступен. Проверьте, что ВМ включена и API-сервер запущен."
            )
        except httpx.TimeoutException as e:
            log.error(f"Чат: таймаут ответа: {e}")
            self.error.emit("Сервер не ответил вовремя. Попробуйте ещё раз.")
        except httpx.HTTPStatusError as e:
            log.error(f"Чат: сервер вернул код {e.response.status_code}: {e}")
            self.error.emit(
                f"Сервер вернул ошибку (код {e.response.status_code}). Детали — в gui.log."
            )
        except Exception as e:
            log.error(f"Чат: неожиданная ошибка: {e}")
            self.error.emit("Непредвиденная ошибка. Детали — в файле gui.log.")


class ChatWidget(QWidget):
    # Сигнал для main_window: True = режим чтения включён, False = выключен
    read_mode_toggled = pyqtSignal(bool)

    def __init__(self, api_client: APIClient):
        super().__init__()
        self.api_client = api_client
        self.read_mode_enabled = False

        self.setStyleSheet("""
            QWidget { background-color: #1e1e1e; color: #ffffff; }
            QTextEdit { background-color: #2b2b2b; color: #ffffff; border: 1px solid #555; border-radius: 4px; }
            QLineEdit { background-color: #3c3c3c; color: white; border: 1px solid #555; padding: 6px; border-radius: 3px; }
            QPushButton { background-color: #0e639c; color: white; border: none; padding: 8px; border-radius: 4px; font-size: 13px; }
            QPushButton:hover { background-color: #1177bb; }
            QLabel { color: #aaaaaa; font-style: italic; }
        """)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        # ============================================================
        # ШАПКА ЧАТА
        # ============================================================
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 6, 10, 6)

        header_label = QLabel("Чат")
        header_label.setStyleSheet(
            "color: #ffffff; font-weight: bold; font-size: 14px; font-style: normal;"
        )
        header_layout.addWidget(header_label)

        header_layout.addStretch()

        self.read_btn = QPushButton("📖 Чтение")
        self.read_btn.setFixedWidth(120)
        self.read_btn.setToolTip("Переключить режим чтения (скрыть панели)")
        self.read_btn.clicked.connect(self.toggle_read_mode)
        header_layout.addWidget(self.read_btn)

        layout.addWidget(header)

        # ============================================================
        # ОБЛАСТЬ ЧАТА
        # ============================================================
        self.chat_area = QTextEdit()
        self.chat_area.setReadOnly(True)
        layout.addWidget(self.chat_area)

        # ============================================================
        # СТАТУС "Бот печатает..." (не скрывается в режиме чтения)
        # ============================================================
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        # ============================================================
        # ПОЛЕ ВВОДА + КНОПКА (в отдельном контейнере для скрытия)
        # ============================================================
        self.input_area = QWidget()
        input_layout = QVBoxLayout(self.input_area)
        input_layout.setContentsMargins(0, 4, 0, 0)
        input_layout.setSpacing(4)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Введите сообщение и нажмите Enter...")
        self.input_field.returnPressed.connect(self.send_message)
        input_layout.addWidget(self.input_field)

        self.send_btn = QPushButton("Отправить")
        self.send_btn.clicked.connect(self.send_message)
        input_layout.addWidget(self.send_btn)

        layout.addWidget(self.input_area)

        self.setLayout(layout)

    # ============================================================
    # РЕЖИМ ЧТЕНИЯ
    # ============================================================
    def toggle_read_mode(self):
        """Переключает режим чтения: скрытие/показ поля ввода."""
        self.read_mode_enabled = not self.read_mode_enabled

        # Скрываем/показываем поле ввода и кнопку отправки
        self.input_area.setVisible(not self.read_mode_enabled)

        # Сообщаем main_window — он скроет/покажет боковую панель
        self.read_mode_toggled.emit(self.read_mode_enabled)

    def exit_read_mode(self):
        """
        Принудительный выход из режима чтения.
        Вызывается main_window при переключении страницы.
        """
        if self.read_mode_enabled:
            self.read_mode_enabled = False
            self.input_area.setVisible(True)
            self.close_read_btn.setVisible(False)
            # НЕ эмитим сигнал — main_window уже в курсе

    # ============================================================
    # ОТПРАВКА СООБЩЕНИЙ
    # ============================================================
    def send_message(self):
        text = self.input_field.text().strip()
        if not text:
            return

        self.chat_area.append(
            f"<div style='text-align: right; color: #4da6ff;'><b>Вы:</b><br>{text}</div>"
        )
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
            chunk = reply[i : i + chunk_size]
            formatted_chunk = chunk.replace("\n", "<br>")
            self.chat_area.append(
                f"<div style='text-align: left; color: #ffffff;'><b>Агент:</b><br>{formatted_chunk}</div>"
            )

    def on_error(self, error_msg: str):
        self.input_field.setEnabled(True)
        self.status_label.setText("")
        self.chat_area.append(
            f"<div style='text-align: left; color: #ff6666;'><b>Ошибка:</b><br>{error_msg}</div>"
        )

    def refresh(self):
        self.chat_area.clear()
        self.chat_area.append(
            "<div style='text-align: center; color: #888;'>История очищена (F5)</div>"
        )
