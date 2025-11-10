import logging
from logging.handlers import RotatingFileHandler
import sys

def setup_logger(name="advisor", logfile="advisor.log"):
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = RotatingFileHandler(logfile, maxBytes=1_000_000, backupCount=3)
        console = logging.StreamHandler(sys.stdout)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[handler, console]
        )
    return logger
