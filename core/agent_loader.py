"""
core/agent_loader.py — загрузка YAML-агентов.

Читает агентов из:
- agents/departments/ — общие промпты отделов
- agents/builtin/ — встроенные агенты (имеют приоритет при конфликте)
- agents/custom/ — пользовательские агенты

Наследование промптов: department_prompt + agent_prompt конкатенируются.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

import yaml
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger("pumka.system")


# ============================================================================
# Pydantic-модели для валидации YAML-агентов
# ============================================================================

class AgentModelConfig(BaseModel):
    """Конфигурация модели агента."""
    provider: str = "ollama"
    name: str
    tier: str = "medium"


class AgentLimitsConfig(BaseModel):
    """Ограничения агента."""
    max_task_time: int = 300
    max_retries: int = 3
    max_parallel_tasks: int = 1


class AgentConfig(BaseModel):
    """Полная конфигурация агента."""
    name: str
    display_name: str
    department: str = "general"
    role: str = ""
    
    model: AgentModelConfig
    system_prompt: str = ""
    tools: List[str] = Field(default_factory=list)
    limits: AgentLimitsConfig = Field(default_factory=AgentLimitsConfig)
    priority: str = "normal"


class DepartmentConfig(BaseModel):
    """Конфигурация отдела."""
    name: str
    display_name: str = ""
    department_prompt: str = ""


# ============================================================================
# Объект агента
# ============================================================================

class Agent:
    """
    Объект агента с полным промптом (department + agent).
    """
    
    def __init__(
        self,
        name: str,
        display_name: str,
        department: str,
        role: str,
        model: AgentModelConfig,
        system_prompt: str,
        tools: List[str],
        limits: AgentLimitsConfig,
        priority: str,
        source: str,  # "builtin", "custom", "department"
    ):
        self.name = name
        self.display_name = display_name
        self.department = department
        self.role = role
        self.model = model
        self.system_prompt = system_prompt  # уже конкатенированный
        self.tools = tools
        self.limits = limits
        self.priority = priority
        self.source = source
    
    def __repr__(self) -> str:
        return (
            f"Agent(name={self.name!r}, display_name={self.display_name!r}, "
            f"department={self.department!r}, source={self.source!r})"
        )


# ============================================================================
# Загрузчик агентов
# ============================================================================

class AgentLoader:
    """Загрузчик YAML-агентов с наследованием."""
    
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.departments_dir = project_root / "agents" / "departments"
        self.builtin_dir = project_root / "agents" / "builtin"
        self.custom_dir = project_root / "agents" / "custom"
        
        self._departments: Dict[str, DepartmentConfig] = {}
        self._agents: Dict[str, Agent] = {}
        
        # Загружаем всё
        self._load_departments()
        self._load_agents()
    
    def _load_yaml(self, path: Path) -> Optional[Dict[str, Any]]:
        """Загружает YAML-файл."""
        if not path.exists():
            return None
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            return data if data else None
        except yaml.YAMLError as e:
            logger.error(f"Ошибка парсинга YAML в {path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Ошибка чтения {path}: {e}")
            return None
    
    def _load_departments(self) -> None:
        """Загружает все отделы из agents/departments/."""
        if not self.departments_dir.exists():
            logger.warning(f"Папка отделов не найдена: {self.departments_dir}")
            return
        
        for yaml_file in self.departments_dir.glob("*.yaml"):
            data = self._load_yaml(yaml_file)
            if not data:
                continue
            
            try:
                dept = DepartmentConfig(**data)
                self._departments[dept.name] = dept
                logger.info(f"Загружен отдел: {dept.name}")
            except ValidationError as e:
                logger.error(f"Некорректный формат отдела в {yaml_file}: {e}")
    
    def _load_agents(self) -> None:
        """
        Загружает всех агентов.
        Builtin имеет приоритет над custom при конфликте имён.
        """
        
        # Сначала загружаем custom
        if self.custom_dir.exists():
            for yaml_file in self.custom_dir.glob("*.yaml"):
                self._load_agent_from_file(yaml_file, source="custom")
        
        # Затем builtin (перезаписывает custom при конфликте)
        if self.builtin_dir.exists():
            for yaml_file in self.builtin_dir.glob("*.yaml"):
                name_from_file = self._get_agent_name_from_file(yaml_file)
                
                if name_from_file and name_from_file in self._agents:
                    # Конфликт: builtin перезаписывает custom
                    logger.warning(
                        f"Конфликт имён: агент '{name_from_file}' есть в custom и builtin. "
                        f"Использую builtin версию. Разрешение через GUI на Этапе 9."
                    )
                
                self._load_agent_from_file(yaml_file, source="builtin")
    
    def _get_agent_name_from_file(self, yaml_file: Path) -> Optional[str]:
        """Извлекает имя агента из YAML без полной загрузки."""
        data = self._load_yaml(yaml_file)
        if data and "name" in data:
            return data["name"]
        return None
    
    def _load_agent_from_file(self, yaml_file: Path, source: str) -> None:
        """Загружает агента из YAML-файла."""
        data = self._load_yaml(yaml_file)
        if not data:
            return
        
        try:
            agent_config = AgentConfig(**data)
        except ValidationError as e:
            logger.error(f"Некорректный формат агента в {yaml_file}: {e}")
            return
        
        # Получаем department_prompt если есть
        department_prompt = ""
        dept = self._departments.get(agent_config.department)
        if dept:
            department_prompt = dept.department_prompt
        
        # Конкатенируем промпты
        full_system_prompt = self._concat_prompts(
            department_prompt,
            agent_config.system_prompt
        )
        
        # Создаём объект агента
        agent = Agent(
            name=agent_config.name,
            display_name=agent_config.display_name,
            department=agent_config.department,
            role=agent_config.role,
            model=agent_config.model,
            system_prompt=full_system_prompt,
            tools=agent_config.tools,
            limits=agent_config.limits,
            priority=agent_config.priority,
            source=source,
        )
        
        self._agents[agent.name] = agent
        logger.info(f"Загружен агент: {agent.name} (source={source})")
    
    def _concat_prompts(self, department_prompt: str, agent_prompt: str) -> str:
        """Конкатенирует промпты отдела и агента."""
        parts = []
        
        if department_prompt:
            parts.append(department_prompt.strip())
        
        if agent_prompt:
            parts.append(agent_prompt.strip())
        
        return "\n\n".join(parts)
    
    def load_agent(self, name: str) -> Optional[Agent]:
        """
        Возвращает агента по имени.
        
        Args:
            name: Имя агента
        
        Returns:
            Объект Agent или None если не найден
        """
        return self._agents.get(name)
    
    def list_agents(self) -> List[Agent]:
        """Возвращает список всех загруженных агентов."""
        return list(self._agents.values())
    
    def get_department(self, name: str) -> Optional[DepartmentConfig]:
        """Возвращает отдел по имени."""
        return self._departments.get(name)
    
    def list_departments(self) -> List[DepartmentConfig]:
        """Возвращает список всех отделов."""
        return list(self._departments.values())


# ============================================================================
# Функция для быстрого доступа
# ============================================================================

_loader_instance: Optional[AgentLoader] = None


def get_loader(project_root: Optional[Path] = None) -> AgentLoader:
    """
    Возвращает экземпляр AgentLoader (singleton).
    
    Args:
        project_root: Путь к корню проекта. Если None — определяется автоматически.
    """
    global _loader_instance
    
    if _loader_instance is None:
        if project_root is None:
            from core.config import _find_project_root
            project_root = _find_project_root()
        
        _loader_instance = AgentLoader(project_root)
    
    return _loader_instance


def load_agent(name: str) -> Optional[Agent]:
    """Загружает агента по имени."""
    return get_loader().load_agent(name)


def list_agents() -> List[Agent]:
    """Возвращает список всех агентов."""
    return get_loader().list_agents()


# ============================================================================
# Точка входа для тестирования: python -m core.agent_loader --list
# ============================================================================

if __name__ == "__main__":
    import sys
    
    from core.logging_setup import setup_logging
    from core.config import load_config
    
    config = load_config()
    setup_logging(config.logs_dir)
    
    # Аргументы командной строки
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        loader = get_loader(config.project_root)
        
        print("=== Список отделов ===")
        departments = loader.list_departments()
        if not departments:
            print("  (отделы не найдены)")
        for dept in departments:
            print(f"  📁 {dept.name}: {dept.display_name}")
        print()
        
        print("=== Список агентов ===")
        agents = loader.list_agents()
        if not agents:
            print("  (агенты не найдены)")
        for agent in agents:
            source_icon = "🔧" if agent.source == "builtin" else "👤"
            print(f"  {source_icon} {agent.name} ({agent.display_name}) — {agent.department}")
            print(f"     Модель: {agent.model.name} (tier: {agent.model.tier})")
            print(f"     Инструменты: {', '.join(agent.tools) if agent.tools else '(нет)'}")
            print()
        
        print(f"Всего: {len(departments)} отделов, {len(agents)} агентов")
    
    else:
        # Тест загрузки конкретного агента
        test_agent_name = "demo"
        agent = load_agent(test_agent_name)
        
        if agent:
            print(f"✅ Агент '{test_agent_name}' загружен успешно")
            print(f"   Имя: {agent.name}")
            print(f"   Отображаемое имя: {agent.display_name}")
            print(f"   Отдел: {agent.department}")
            print(f"   Модель: {agent.model.name}")
            print(f"   Tier: {agent.model.tier}")
            print(f"   Инструменты: {agent.tools}")
            print()
            print("   Системный промпт (первые 300 символов):")
            print(f"   {agent.system_prompt[:300]}...")
        else:
            print(f"❌ Агент '{test_agent_name}' не найден")
            print()
            print("Доступные агенты:")
            for a in list_agents():
                print(f"  - {a.name}")