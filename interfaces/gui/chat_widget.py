"""
interfaces/gui/chat_widget.py — виджет чата с мультичатом.
Слева — список чатов (200px), справа — лента чата с историей.
Добавлен режим чтения: кнопка «📖 Чтение» в шапке + плавающая «✕» в углу.
"""
import httpx
import json
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLineEdit,
    QPushButton, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QSizePolicy,
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer, pyqtSlot
from interfaces.gui.api_client import APIClient
from interfaces.gui.gui_log import get_gui_logger
from interfaces.gui.local_secret import APP_DIR

# Файл для сохранения состояния GUI (выбранный чат)
GUI_STATE_FILE = APP_DIR / "gui_state.json"


class ChatWorker(QThread):
    """Отдельный поток, чтобы интерфейс не зависал во время ожидания ответа."""
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, api_client: APIClient, message: str, chat_id: str = ""):
        super().__init__()
        self.api_client = api_client
        self.message = message
        self.chat_id = chat_id

    def run(self):
        log = get_gui_logger()
        try:
            response = self.api_client.send_chat_message(self.message, self.chat_id)
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


class TitleLineEdit(QLineEdit):
    """
    QLineEdit с сигналом редактирования после дебаунса (500 мс).
    Автосохранение заголовка при потере фокуса.
    """
    title_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._debounce_timer = QTimer()
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(500)
        self._debounce_timer.timeout.connect(self._emit_change)
        self._last_text = ""

    def focusOutEvent(self, event):
        """При потере фокуса — сохранить заголовок."""
        super().focusOutEvent(event)
        self._save_now()

    def setText(self, text):
        super().setText(text)
        self._last_text = text

    def _save_now(self):
        """Немедленное сохранение (при потере фокуса)."""
        current = self.text().strip()
        if current and current != self._last_text:
            self._last_text = current
            self.title_changed.emit(current)

    def _emit_change(self):
        """Срабатывает по таймеру дебаунса."""
        self._save_now()

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
        # При вводе текста — сбрасываем таймер дебаунса
        self._debounce_timer.start()


class ChatWidget(QWidget):
    # Сигнал для main_window: True = режим чтения включён, False = выключен
    read_mode_toggled = pyqtSignal(bool)

    def __init__(self, api_client: APIClient):
        super().__init__()
        self.api_client = api_client
        self.read_mode_enabled = False
        self.current_chat_id = ""
        self.log = get_gui_logger()

        self._load_gui_state()

        self.setStyleSheet("""
            QWidget { background-color: #1e1e1e; color: #ffffff; }
            QTextEdit { background-color: #2b2b2b; color: #ffffff; border: 1px solid #555; border-radius: 4px; }
            QLineEdit { background-color: #3c3c3c; color: white; border: 1px solid #555; padding: 6px; border-radius: 3px; }
            QPushButton { background-color: #0e639c; color: white; border: none; padding: 8px; border-radius: 4px; font-size: 13px; }
            QPushButton:hover { background-color: #1177bb; }
            QLabel { color: #aaaaaa; font-style: italic; }
            QListWidget { background-color: #252526; color: #ffffff; border: none; font-size: 13px; }
            QListWidget::item { padding: 10px; border-bottom: 1px solid #333; }
            QListWidget::item:selected { background-color: #0e639c; }
            QListWidget::item:hover { background-color: #2a2d2e; }
        """)

        self._build_ui()
        self._load_chats_list()

        # Если был сохранён выбранный чат — загружаем его
        if self.current_chat_id:
            self._select_chat_by_id(self.current_chat_id)

    # ============================================================
    # ПОСТРОЕНИЕ UI
    # ============================================================
    def _build_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        # Шапка чата
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

        # Основная область: список чатов + лента
        main_area = QWidget()
        main_layout = QHBoxLayout(main_area)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # === ЛЕВАЯ ПАНЕЛЬ: список чатов ===
        left_panel = QWidget()
        left_panel.setFixedWidth(200)
        left_panel.setStyleSheet("QWidget { background-color: #252526; }")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self.new_chat_btn = QPushButton("➕ Новый чат")
        self.new_chat_btn.clicked.connect(self.create_new_chat)
        left_layout.addWidget(self.new_chat_btn)

        self.chats_list = QListWidget()
        self.chats_list.currentItemChanged.connect(self._on_chat_selected)
        left_layout.addWidget(self.chats_list)

        # Плашка ошибки загрузки
        self.chats_error_label = QLabel("Не удалось загрузить чаты")
        self.chats_error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chats_error_label.setStyleSheet(
            "color: #ff6666; padding: 10px; background-color: #3a1a1a;"
        )
        self.chats_error_label.hide()
        left_layout.addWidget(self.chats_error_label)

        main_layout.addWidget(left_panel)

        # === ПРАВАЯ ОБЛАСТЬ: чат ===
        self.right_panel = QWidget()
        right_layout = QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(10, 10, 10, 10)

        # Поле заголовка чата (редактируемое)
        self.title_edit = TitleLineEdit()
        self.title_edit.setPlaceholderText("Название чата")
        self.title_edit.setEnabled(False)
        self.title_edit.title_changed.connect(self._on_title_changed)
        right_layout.addWidget(self.title_edit)

        # Лента чата
        self.chat_area = QTextEdit()
        self.chat_area.setReadOnly(True)
        right_layout.addWidget(self.chat_area)

        # Пустое состояние
        self.empty_state_label = QLabel("Нет чатов. Создайте первый!")
        self.empty_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state_label.setStyleSheet(
            "color: #888; font-size: 16px; padding: 40px;"
        )
        self.empty_state_label.hide()
        right_layout.addWidget(self.empty_state_label)

        # Подсказка "Сначала создайте чат"
        self.hint_label = QLabel("Сначала создайте чат (кнопка слева)")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setStyleSheet(
            "color: #ffcc00; background-color: #333; padding: 10px; border-radius: 4px;"
        )
        self.hint_label.hide()
        right_layout.addWidget(self.hint_label)

        # Статус "Бот печатает..."
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #aaaaaa; font-style: italic;")
        right_layout.addWidget(self.status_label)

        # Поле ввода + кнопка
        self.input_area = QWidget()
        input_layout = QVBoxLayout(self.input_area)
        input_layout.setContentsMargins(0, 4, 0, 0)
        input_layout.setSpacing(4)
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Введите сообщение и нажмите Enter...")
        self.input_field.returnPressed.connect(self.send_message)
        self.input_field.setEnabled(False)
        input_layout.addWidget(self.input_field)
        self.send_btn = QPushButton("Отправить")
        self.send_btn.clicked.connect(self.send_message)
        self.send_btn.setEnabled(False)
        input_layout.addWidget(self.send_btn)
        right_layout.addWidget(self.input_area)

        main_layout.addWidget(self.right_panel)
        layout.addWidget(main_area)
        self.setLayout(layout)

    # ============================================================
    # РАБОТА СО СПИСКОМ ЧАТОВ
    # ============================================================
    def _load_chats_list(self):
        """Загружает список чатов с сервера."""
        try:
            chats = self.api_client.list_chats()
            self.chats_list.clear()
            self.chats_error_label.hide()

            if not chats:
                self._show_empty_state()
                return

            self._hide_empty_state()
            for chat in chats:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, chat["chat_id"])
                # Отображаем: заголовок + фрагмент последнего сообщения
                last_msg = chat.get("last_message", "Без сообщений")
                if len(last_msg) > 30:
                    last_msg = last_msg[:30] + "..."
                item.setText(f"{chat['title']}\n{last_msg}")
                self.chats_list.addItem(item)

            self.log.info(f"Список чатов загружен: {len(chats)} чатов")

        except Exception as e:
            self.log.error(f"Ошибка загрузки списка чатов: {e}")
            self.chats_error_label.show()

    def _show_empty_state(self):
        self.empty_state_label.show()
        self.chat_area.hide()
        self.input_area.hide()
        self.title_edit.hide()
        self.status_label.hide()

    def _hide_empty_state(self):
        self.empty_state_label.hide()
        self.chat_area.show()
        self.input_area.show()
        self.title_edit.show()
        self.status_label.show()

    def _on_chat_selected(self, current, previous):
        """Срабатывает при выборе чата в списке."""
        if not current:
            return
        chat_id = current.data(Qt.ItemDataRole.UserRole)
        if not chat_id:
            return

        # Блокируем сигналы, чтобы не было рекурсии
        self.chats_list.blockSignals(True)
        self._load_chat_history(chat_id)
        self.chats_list.blockSignals(False)

    def _load_chat_history(self, chat_id: str):
        """Загружает историю чата с сервера."""
        try:
            chat_data = self.api_client.get_chat(chat_id)
            self.current_chat_id = chat_id
            self._save_gui_state()

            # Обновляем заголовок
            self.title_edit.setEnabled(True)
            self.title_edit.blockSignals(True)
            self.title_edit.setText(chat_data["title"])
            self.title_edit.blockSignals(False)
            self.title_edit._last_text = chat_data["title"]

            # Очищаем ленту и загружаем историю
            self.chat_area.clear()
            self._hide_empty_state()
            self.hint_label.hide()

            for msg in chat_data["messages"]:
                self._render_message(msg["role"], msg["content"])

            # Прокручиваем вниз
            scrollbar = self.chat_area.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

            # Разблокируем поле ввода
            self.input_field.setEnabled(True)
            self.send_btn.setEnabled(True)
            self.input_field.setFocus()

            self.log.info(f"Выбран чат: {chat_id} ({chat_data['title']})")

        except Exception as e:
            self.log.error(f"Ошибка загрузки истории чата {chat_id}: {e}")
            self.chat_area.clear()
            self.chat_area.append(
                f"<div style='text-align: center; color: #ff6666;'>"
                f"Не удалось загрузить историю чата.<br>{e}</div>"
            )

    def _select_chat_by_id(self, chat_id: str):
        """Выбирает чат по ID (например, при запуске после сохранения)."""
        for i in range(self.chats_list.count()):
            item = self.chats_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == chat_id:
                self.chats_list.setCurrentItem(item)
                return
        # Чат не найден — очищаем состояние
        self.log.warning(f"Сохранённый чат {chat_id} не найден, очищаем состояние")
        self.current_chat_id = ""
        self._save_gui_state()

    def create_new_chat(self):
        """Создаёт новый чат через API."""
        try:
            result = self.api_client.create_chat()
            self.log.info(f"Создан новый чат: {result['chat_id']}")
            # Перезагружаем список и выбираем новый чат
            self._load_chats_list()
            self._select_chat_by_id(result["chat_id"])
        except Exception as e:
            self.log.error(f"Ошибка создания чата: {e}")
            QMessageBox.critical(
                self, "Ошибка",
                f"Не удалось создать чат.\n{e}"
            )

    # ============================================================
    # РЕДАКТИРОВАНИЕ ЗАГОЛОВКА
    # ============================================================
    @pyqtSlot(str)
    def _on_title_changed(self, new_title: str):
        """Срабатывает при изменении заголовка (после дебаунса или потери фокуса)."""
        if not self.current_chat_id:
            return
        try:
            self.api_client.update_chat_title(self.current_chat_id, new_title)
            self.log.info(f"Заголовок чата обновлён: {new_title}")
            # Обновляем элемент в списке
            self._update_current_list_item(new_title)
        except Exception as e:
            self.log.error(f"Ошибка сохранения заголовка: {e}")
            # Возвращаем старое значение
            QMessageBox.warning(
                self, "Ошибка",
                f"Не удалось сохранить заголовок.\n{e}"
            )
            # Загружаем актуальное значение с сервера
            try:
                chat_data = self.api_client.get_chat(self.current_chat_id)
                self.title_edit.blockSignals(True)
                self.title_edit.setText(chat_data["title"])
                self.title_edit._last_text = chat_data["title"]
                self.title_edit.blockSignals(False)
            except Exception:
                pass

    def _update_current_list_item(self, new_title: str):
        """Обновляет текст выбранного элемента в списке чатов."""
        item = self.chats_list.currentItem()
        if item:
            # Сохраняем last_message (вторая строка)
            old_text = item.text()
            lines = old_text.split("\n")
            last_msg = lines[1] if len(lines) > 1 else "Без сообщений"
            item.setText(f"{new_title}\n{last_msg}")

    # ============================================================
    # ОТПРАВКА СООБЩЕНИЙ
    # ============================================================
    def send_message(self):
        text = self.input_field.text().strip()
        if not text:
            return

        if not self.current_chat_id:
            self.hint_label.show()
            QTimer.singleShot(2000, self.hint_label.hide)
            return

        self.chat_area.append(
            f"<div style='text-align: right; color: #4da6ff;'><b>Вы:</b><br>{text}</div>"
        )
        self.input_field.clear()
        self.input_field.setEnabled(False)
        self.send_btn.setEnabled(False)
        self.status_label.setText("Бот печатает...")

        self.worker = ChatWorker(self.api_client, text, self.current_chat_id)
        self.worker.finished.connect(self.on_response)
        self.worker.error.connect(self.on_error)
        self.worker.start()

    def on_response(self, reply: str):
        self.input_field.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.status_label.setText("")

        chunk_size = 4096
        for i in range(0, len(reply), chunk_size):
            chunk = reply[i : i + chunk_size]
            formatted_chunk = chunk.replace("\n", "<br>")
            self.chat_area.append(
                f"<div style='text-align: left; color: #ffffff;'><b>Агент:</b><br>{formatted_chunk}</div>"
            )

        # Прокручиваем вниз
        scrollbar = self.chat_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

        # Обновляем список чатов (чтобы показать новый last_message)
        self._load_chats_list()

        # Восстанавливаем выбор
        if self.current_chat_id:
            self._select_chat_by_id(self.current_chat_id)

    def on_error(self, error_msg: str):
        self.input_field.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.status_label.setText("")
        self.chat_area.append(
            f"<div style='text-align: left; color: #ff6666;'><b>Ошибка:</b><br>{error_msg}</div>"
        )

    def _render_message(self, role: str, content: str):
        """Отрисовывает одно сообщение в ленте."""
        if role == "user":
            formatted = content.replace("\n", "<br>")
            self.chat_area.append(
                f"<div style='text-align: right; color: #4da6ff;'><b>Вы:</b><br>{formatted}</div>"
            )
        elif role == "assistant":
            formatted = content.replace("\n", "<br>")
            self.chat_area.append(
                f"<div style='text-align: left; color: #ffffff;'><b>Агент:</b><br>{formatted}</div>"
            )

    # ============================================================
    # РЕЖИМ ЧТЕНИЯ
    # ============================================================
    def toggle_read_mode(self):
        """Переключает режим чтения: скрытие/показ панелей."""
        self.read_mode_enabled = not self.read_mode_enabled

        # Скрываем/показываем левую панель, поле ввода и заголовок
        self.right_panel.parent().layout().itemAt(0).widget().setVisible(
            not self.read_mode_enabled
        )
        self.title_edit.setVisible(not self.read_mode_enabled)
        self.input_area.setVisible(not self.read_mode_enabled)

        self.read_mode_toggled.emit(self.read_mode_enabled)

    def exit_read_mode(self):
        """Принудительный выход из режима чтения."""
        if self.read_mode_enabled:
            self.read_mode_enabled = False
            self.right_panel.parent().layout().itemAt(0).widget().setVisible(True)
            self.title_edit.setVisible(True)
            self.input_area.setVisible(True)
            self.read_mode_toggled.emit(False)

    # ============================================================
    # СОХРАНЕНИЕ СОСТОЯНИЯ GUI
    # ============================================================
    def _save_gui_state(self):
        """Сохраняет текущий выбранный чат в gui_state.json."""
        try:
            state = {"selected_chat_id": self.current_chat_id}
            GUI_STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
        except Exception as e:
            self.log.error(f"Не удалось сохранить состояние GUI: {e}")

    def _load_gui_state(self):
        """Загружает сохранённое состояние GUI."""
        try:
            if GUI_STATE_FILE.exists():
                state = json.loads(GUI_STATE_FILE.read_text(encoding="utf-8"))
                self.current_chat_id = state.get("selected_chat_id", "")
        except Exception as e:
            self.log.error(f"Не удалось загрузить состояние GUI: {e}")
            self.current_chat_id = ""

    # ============================================================
    # ОБНОВЛЕНИЕ (F5)
    # ============================================================
    def refresh(self):
        """Перезагружает список чатов и историю текущего чата (клавиша F5)."""
        self._load_chats_list()
        if self.current_chat_id:
            self._select_chat_by_id(self.current_chat_id)
        else:
            self.chat_area.clear()
            self.chat_area.append(
                "<div style='text-align: center; color: #888;'>История очищена (F5)</div>"
            )
