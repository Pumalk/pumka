import httpx
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLineEdit, 
                             QComboBox, QPushButton, QMessageBox, QLabel, QScrollArea)
from PyQt6.QtCore import Qt
from interfaces.gui.api_client import APIClient
from interfaces.gui.gui_log import get_gui_logger

class SettingsWidget(QWidget):
    def __init__(self, api_client: APIClient):
        super().__init__()
        self.api_client = api_client
        
        self.setStyleSheet("""
            QWidget { background-color: #1e1e1e; color: #ffffff; }
            QLabel { color: #ffffff; }
            QLineEdit { background-color: #3c3c3c; color: white; border: 1px solid #555; padding: 6px; border-radius: 3px; }
            QComboBox { background-color: #3c3c3c; color: white; border: 1px solid #555; padding: 6px; border-radius: 3px; }
            QComboBox::drop-down { border: none; }
            QPushButton { background-color: #0e639c; color: white; border: none; padding: 8px; border-radius: 4px; font-size: 13px; }
            QPushButton:hover { background-color: #1177bb; }
        """)
        
        # Прокручиваемая область на случай, если окно будет маленьким
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background-color: #1e1e1e; }")
        
        content = QWidget()
        layout = QVBoxLayout(content)
        
        form_layout = QFormLayout()
        
        self.server_url = QLineEdit(self.api_client.base_url)
        form_layout.addRow("Адрес сервера:", self.server_url)
        
        self.vault_path = QLineEdit()
        form_layout.addRow("Путь к vault:", self.vault_path)
        
        self.projects_path = QLineEdit()
        form_layout.addRow("Путь к projects:", self.projects_path)
        
        self.ollama_url = QLineEdit()
        form_layout.addRow("URL Ollama:", self.ollama_url)
        
        self.profile_combo = QComboBox()
        self.profile_combo.addItems(["light", "balanced", "power"])
        form_layout.addRow("LLM Профиль:", self.profile_combo)
        
        layout.addLayout(form_layout)
        
        self.save_btn = QPushButton("Сохранить")
        self.save_btn.clicked.connect(self.save_settings)
        layout.addWidget(self.save_btn)
        
        self.info_label = QLabel("Сохранённые изменения применятся на следующих этапах.")
        self.info_label.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self.info_label)
        
        layout.addStretch()
        scroll.setWidget(content)
        
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(scroll)
        
        self.load_settings()

    def load_settings(self):
        """Загружает текущие настройки с сервера и заполняет поля."""
        try:
            config = self.api_client.get_config()
            paths = config.get("paths", {})
            llm = config.get("llm", {})
            self.vault_path.setText(paths.get("vault", ""))
            self.projects_path.setText(paths.get("projects", ""))
            self.ollama_url.setText(llm.get("ollama_url", ""))
            profile = llm.get("default_profile", "balanced")
            index = self.profile_combo.findText(profile)
            if index >= 0:
                self.profile_combo.setCurrentIndex(index)
        except Exception:
            # Если сервер недоступен, оставляем поля пустыми
            pass

    def save_settings(self):
        """Отправляет настройки на сервер."""
        # Обновляем адрес сервера в клиенте
        self.api_client.base_url = self.server_url.text().rstrip("/")
        
        data = {
            "vault_path": self.vault_path.text(),
            "projects": self.projects_path.text(),
            "ollama_url": self.ollama_url.text(),
            "profile": self.profile_combo.currentText()
        }
        try:
            self.api_client.update_config(data)
            QMessageBox.information(self, "Сохранено", 
                "Настройки сохранены (применится на следующих этапах).")
        except httpx.ConnectError as e:
            get_gui_logger().error(f"Настройки: сервер недоступен: {e}")
            QMessageBox.critical(self, "Ошибка",
                "Сервер недоступен. Проверьте, что ВМ включена и API-сервер запущен.")
        except Exception as e:
            get_gui_logger().error(f"Настройки: ошибка сохранения: {e}")
            QMessageBox.critical(self, "Ошибка",
                "Не удалось сохранить настройки. Детали — в файле gui.log.")

    def refresh(self):
        """Перезагружает настройки с сервера (для клавиши F5)."""
        self.load_settings()