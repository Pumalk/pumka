import logging
from interfaces.gui.local_secret import APP_DIR

def get_gui_logger() -> logging.Logger:
    """Возвращает логгер, пишущий технические детали в %APPDATA%\\Pumka\\gui.log."""
    logger = logging.getLogger("pumka.gui")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        handler = logging.FileHandler(APP_DIR / "gui.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
        logger.propagate = False
    return logger