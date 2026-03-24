import logging
import os
import sys

_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
_configured = False


def configure_logging(level: str | None = None, log_file: str | None = None, console: bool = True) -> None:
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    if root.handlers:
        _configured = True
        return

    level_name = (level or os.getenv("ADVISOR_LOG_LEVEL", "INFO")).upper()
    resolved_level = getattr(logging, level_name, logging.INFO)
    log_file = log_file if log_file is not None else os.getenv("ADVISOR_LOG_FILE", "MA_DynamAdvisor.log")

    handlers: list[logging.Handler] = []
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    if console:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(level=resolved_level, format=_DEFAULT_FORMAT, handlers=handlers)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
