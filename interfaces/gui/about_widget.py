from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTextEdit, QScrollArea
from PyQt6.QtCore import Qt
from interfaces.gui.api_client import APIClient

HOTKEYS_TEXT = """
<table style='color:#ffffff; font-size:13px;'>
<tr><td><b>Ctrl+1</b></td><td>Чат</td></tr>
<tr><td><b>Ctrl+2</b></td><td>Настройки</td></tr>
<tr><td><b>Ctrl+3</b></td><td>О проекте</td></tr>
<tr><td><b>Ctrl+K</b></td><td>Поиск (заглушка)</td></tr>
<tr><td><b>F5</b></td><td>Обновить текущий раздел</td></tr>
</table>
"""

class AboutWidget(QWidget):
    def __init__(self, api_client: APIClient):
        super().__init__()
        self.api_client = api_client
        
        self.setStyleSheet("""
            QWidget { background-color: #1e1e1e; color: #ffffff; }
            QLabel { color: #ffffff; }
            QTextEdit { background-color: #2b2b2b; color: #ffffff; border: 1px solid #555; border-radius: 4px; }
        """)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background-color: #1e1e1e; }")
        
        content = QWidget()
        layout = QVBoxLayout(content)
        
        title = QLabel("Pumka — личный ИИ-ассистент")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        version = QLabel("Версия: pumka-0.0.38 (Этап 3)")
        version.setStyleSheet("color: #aaaaaa;")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)
        
        disclaimer = QLabel("Проект делается исключительно для личного использования автора. Монетизация не прорабатывается.")
        disclaimer.setStyleSheet("color: #ffcc00; background-color: #333; padding: 8px; border-radius: 4px;")
        disclaimer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        disclaimer.setWordWrap(True)
        layout.addWidget(disclaimer)
        
        layout.addWidget(QLabel("<br><b>Список агентов:</b>"))
        self.agents_text = QTextEdit()
        self.agents_text.setReadOnly(True)
        self.agents_text.setMaximumHeight(200)
        layout.addWidget(self.agents_text)
        
        layout.addWidget(QLabel("<br><b>Горячие клавиши:</b>"))
        hotkeys_label = QLabel(HOTKEYS_TEXT)
        layout.addWidget(hotkeys_label)
        
        layout.addStretch()
        scroll.setWidget(content)
        
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(scroll)
        
        self.load_agents()

    def load_agents(self):
        """Загружает список агентов с сервера."""
        try:
            agents = self.api_client.get_agents()
            if not agents:
                self.agents_text.setText("Агенты не найдены.")
                return
            lines = []
            for a in agents:
                lines.append(f"<b>{a['display_name']}</b> ({a['name']}) — "
                             f"отдел: {a['department']}, роль: {a['role']}, tier: {a['tier']}")
            self.agents_text.setHtml("<br>".join(lines))
        except Exception as e:
            self.agents_text.setText(f"Не удалось загрузить агентов:\n{e}")

    def refresh(self):
        """Перезагружает список агентов (для клавиши F5)."""
        self.load_agents()