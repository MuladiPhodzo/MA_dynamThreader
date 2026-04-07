import json
from datetime import datetime
from pathlib import Path

from advisor.utils.logging_setup import get_logger
from advisor.core.locks import STATE_LOCK


logger = get_logger("StateStore")

STATE_FILE = Path("bot_state.json")

class StateStore:

    def __init__(self):
        self.state = self._load()
    
    @staticmethod
    def _load() -> dict:
        with STATE_LOCK:
            if not STATE_FILE.exists():
                STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                return {}
            try:
                if STATE_FILE.stat().st_size == 0:
                    logger.warning("State file is empty. Resetting to defaults.")
                    StateStore._write_default()
                    return {}
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                logger.critical("State file corrupted. Resetting. Error: %s", e)
                StateStore._backup_corrupt()
                StateStore._write_default()
                return {}
            except Exception as e:
                logger.exception("Failed to load state file: %s", e)
                return {}

    def _write_default(self):
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({}, f)
        except Exception:
            logger.exception("Failed to write default state file.")

    def _backup_corrupt(self):
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = f"{STATE_FILE}.corrupt.{ts}"
            STATE_FILE.replace(backup)
            logger.warning("Backed up corrupt state file to %s", backup)
        except Exception:
            logger.exception("Failed to back up corrupt state file.")

    @staticmethod
    def save_bot_state(state: dict):

        with STATE_LOCK:
            tmp = STATE_FILE.with_suffix(".tmp")

            try:
                with open(tmp, "w") as f:
                    json.dump(state, f, indent=2)

                tmp.replace(STATE_FILE)

            except Exception as e:
                logger.error(f"Failed to persist bot state: {e}")

    def get(self, key, default=None):
        return self.state.get(key, default)

    def set(self, key, value):
        self.state[key] = value
        StateStore.save_bot_state(self.state)
