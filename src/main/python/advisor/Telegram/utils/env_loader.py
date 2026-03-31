# advisor/Telegram/utils/env_loader.py
from dotenv import load_dotenv
from pathlib import Path
import os
import sys
from advisor.utils.logging_setup import get_logger

logger = get_logger(__name__)

def load_env():
    """Load TELEGRAM_BOT_TOKEN from .env file (root or src/main)."""
    possible_paths = [
        Path.cwd() / ".env",
        Path.cwd() / "src" / "main" / ".env",
        Path(__file__).parents[4] / ".env",   # fallback for packaged builds
    ]

    for env_path in possible_paths:
        if env_path.exists():
            logger.info(f"🌱 Loading environment from: {env_path}")
            load_dotenv(dotenv_path=env_path, override=True)
            break

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.info("⚠️ TELEGRAM_BOT_TOKEN not found in any .env file.")
    return token
