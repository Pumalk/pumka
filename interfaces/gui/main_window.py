from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                             QListWidget, QStackedWidget, QMessageBox)
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtCore import Qt
from interfaces.gui.chat_widget import ChatWidget
from interfaces.gui.settings_widget import SettingsWidget
from interfaces.gui.about_widget import AboutWidget
from interfaces.gui.api_client import APIClient

# Единая тёмная тема для всего главного окна (QSS)
DARK_QSS = """
QMainWindow { background-color: #1e1e1e; }
QWidget { background-color: #1e1e1e; color: #ffffff; font-size: 13px; }
QListWidget { background-color: #252526; color: #ffffff; border: none; font-size: 14px; }
QListWidget::item { padding: 12px; }
QListWidget::item:selected { background-color: #0e639c; }
QListWidget::item:hover { background-color: #2a2d2e; }
"""

class MainWindow(QMainWindow):
    def __init__(self, api_client: APIClient):
        super().__init__()
        self.api_client = api_client
        self.setWindowTitle("Pumka")
        self.resize(1000, 700)
        self.setStyleSheet(DARK_QSS)
        
        self._setup_hotkeys()
        self._setup_ui()

    def _setup_hotkeys(self):
        """Настраивает горячие клавиши через QAction."""
        act_chat = QAction(self)
        act_chat.setShortcut(QKeySequence("Ctrl+1"))
        act_chat.triggered.connect(lambda: self.switch_to(0))
        self.addAction(act_chat)

        act_settings = QAction(self)
        act_settings.setShortcut(QKeySequence("Ctrl+2"))
        act_settings.triggered.connect(lambda: self.switch_to(1))
        self.addAction(act_settings)

        act_about = QAction(self)
        act_about.setShortcut(QKeySequence("Ctrl+3"))
        act_about.triggered.connect(lambda: self.switch_to(2))
        self.addAction(act_about)

        act_search = QAction(self)
        act_search.setShortcut(QKeySequence("Ctrl+K"))
        act_search.triggered.connect(self.show_search_stub)
        self.addAction(act_search)

        act_refresh = QAction(self)
        act_refresh.setShortcut(QKeySequence("F5"))
        act_refresh.triggered.connect(self.refresh_current)
        self.addAction(act_refresh)

    def _setup_ui(self):
        """Создаёт боковую панель и область контента."""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Боковая панель
        self.sidebar = QListWidget()
        self.sidebar.addItems(["Чат", "Настройки", "О проекте"])
        self.sidebar.setFixedWidth(180)
        self.sidebar.currentRowChanged.connect(self._on_sidebar_change)
        layout.addWidget(self.sidebar)

        # Область контента (переключаемые страницы)
        self.stack = QStackedWidget()
        self.chat_widget = ChatWidget(self.api_client)
        self.settings_widget = SettingsWidget(self.api_client)
        self.about_widget = AboutWidget(self.api_client)
        self.stack.addWidget(self.chat_widget)
        self.stack.addWidget(self.settings_widget)
        self.stack.addWidget(self.about_widget)
        layout.addWidget(self.stack)

        # По умолчанию открываем Чат
        self.sidebar.setCurrentRow(0)

    def switch_to(self, index):
        """Переключает на страницу по индексу."""
        self.sidebar.setCurrentRow(index)

    def _on_sidebar_change(self, index):
        """Срабатывает при выборе пункта в боковой панели."""
        self.stack.setCurrentIndex(index)

    def show_search_stub(self):
        """Заглушка для поиска (появится на следующих этапах)."""
        QMessageBox.information(self, "Поиск", 
            "Функция поиска появится на следующих этапах.")

    def refresh_current(self):
        """Обновляет текущую страницу (клавиша F5)."""
        current = self.stack.currentWidget()
        if hasattr(current, "refresh"):
            current.refresh()