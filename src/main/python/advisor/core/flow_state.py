import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from advisor.indicators.signal_store import SignalStore
from advisor.utils.logging_setup import get_logger

logger = get_logger("FlowStateStore")


class FlowStateStore:
    FILE = Path("runtime/flow_state.json")

    def __init__(self):
        self._lock = threading.Lock()
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.FILE.exists():
            self.FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.FILE, "w", encoding="utf-8") as handle:
                json.dump({}, handle)
            return {}

        try:
            with open(self.FILE, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            return raw if isinstance(raw, dict) else {}
        except Exception as exc:
            logger.warning("Failed to load flow state: %s", exc)
            return {}

    def _persist(self) -> None:
        self.FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.FILE.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(self._state, handle, indent=2)
            tmp.replace(self.FILE)
        except Exception as exc:
            logger.warning("Failed to persist flow state: %s", exc)

    def get_section(self, name: str, default: Any | None = None) -> Any:
        with self._lock:
            return self._state.get(name, default)

    def set_section(self, name: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._state[name] = dict(data)
            self._persist()

    def update_section(self, name: str, data: dict[str, Any]) -> None:
        with self._lock:
            current = self._state.get(name)
            if not isinstance(current, dict):
                current = {}
            current.update(data)
            self._state[name] = current
            self._persist()

    def save_signal_store(self, store: SignalStore, max_age_minutes: int = 15) -> None:
        payload = store.snapshot_latest(max_age_minutes=max_age_minutes)
        self.update_section(
            "signals",
            {"latest": payload, "saved_at": datetime.now(timezone.utc).isoformat()},
        )

    def restore_signal_store(self, store: SignalStore) -> None:
        section = self.get_section("signals", {})
        latest = section.get("latest") if isinstance(section, dict) else None
        if isinstance(latest, dict) and latest:
            store.load_latest(latest)

    def save_processed_signals(self, signal_ids: set[str], max_items: int = 1000) -> None:
        items = list(signal_ids)
        if len(items) > max_items:
            items = items[-max_items:]
        self.update_section(
            "execution",
            {"processed_signals": items, "saved_at": datetime.now(timezone.utc).isoformat()},
        )

    def load_processed_signals(self) -> set[str]:
        section = self.get_section("execution", {})
        if not isinstance(section, dict):
            return set()
        raw = section.get("processed_signals", [])
        if not isinstance(raw, list):
            return set()
        return {str(item) for item in raw}
