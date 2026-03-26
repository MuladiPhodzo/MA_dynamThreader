import json
import os
from datetime import datetime
from advisor.utils.logging_setup import get_logger

logger = get_logger("StateStore")


class StateStore:

    def __init__(self, path="bot_state.json"):
        self.path = path
        self.state = self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return {}
        try:
            if os.path.getsize(self.path) == 0:
                logger.warning("State file is empty. Resetting to defaults.")
                self._write_default()
                return {}
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.critical("State file corrupted. Resetting. Error: %s", e)
            self._backup_corrupt()
            self._write_default()
            return {}
        except Exception as e:
            logger.exception("Failed to load state file: %s", e)
            return {}

    def _write_default(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({}, f)
        except Exception:
            logger.exception("Failed to write default state file.")

    def _backup_corrupt(self):
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = f"{self.path}.corrupt.{ts}"
            os.replace(self.path, backup)
            logger.warning("Backed up corrupt state file to %s", backup)
        except Exception:
            logger.exception("Failed to back up corrupt state file.")

    def save(self):
        with open(self.path, "w") as f:
            json.dump(self.state, f, indent=4)

    def get(self, key, default=None):
        return self.state.get(key, default)

    def set(self, key, value):
        self.state[key] = value
        self.save()
