from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Dict, Any

try:
    import pandas as pd
except Exception:  # pragma: no cover - optional for runtime environments without pandas
    pd = None  # type: ignore[assignment]


class CacheManager:

    def __init__(self, ttl: int = 600, persist: bool = False, persist_dir: str = "runtime/cache"):
        self.ttl = ttl
        self.memory: Dict[str, Any] = {}
        self.timestamps: Dict[str, float] = {}
        self.persist = bool(persist)
        self.persist_dir = Path(persist_dir)

        self.lock = threading.RLock()
        self.file_lock = threading.RLock()

    def set(self, key: str, value: Any):

        with self.lock:
            self.memory[key] = value
            self.timestamps[key] = time.time()

    def set_atomic(self, key: str, value: Any):
        # Thread-safe set + optional disk persistence.
        self.set(key, value)
        if self.persist:
            self._persist_to_disk(key, value)

    def get(self, key: str):

        with self.lock:

            if key not in self.memory:
                pass
            else:
                if time.time() - self.timestamps.get(key, 0) > self.ttl:
                    self.memory.pop(key, None)
                    self.timestamps.pop(key, None)
                else:
                    return self.memory[key]

        if self.persist:
            return self._load_from_disk(key)

        return None

    def get_by_group(self, group: str):

        result = {}

        with self.lock:

            expired = []

            for key, value in self.memory.items():

                if group not in key:
                    continue

                if time.time() - self.timestamps.get(key, 0) > self.ttl:
                    expired.append(key)
                    continue

                result[key] = value

            for key in expired:
                self.memory.pop(key, None)
                self.timestamps.pop(key, None)

        return result

    def _safe_key(self, key: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", key)

    def _payload_path(self, key: str) -> Path:
        safe_key = self._safe_key(key)
        return self.persist_dir / f"{safe_key}.json"

    def _serialize_value(self, value: Any) -> Any:
        if pd is not None and isinstance(value, pd.DataFrame):
            return {
                "__type__": "dataframe",
                "value": value.to_json(orient="split", date_format="iso"),
            }
        if isinstance(value, dict):
            return {"__type__": "dict", "value": {k: self._serialize_value(v) for k, v in value.items()}}
        if isinstance(value, list):
            return {"__type__": "list", "value": [self._serialize_value(v) for v in value]}
        return value

    def _deserialize_value(self, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        marker = payload.get("__type__")
        if marker == "dataframe":
            if pd is None:
                return None
            data = payload.get("value")
            try:
                return pd.read_json(data, orient="split")
            except Exception:
                return None
        if marker == "dict":
            value = payload.get("value", {})
            return {k: self._deserialize_value(v) for k, v in value.items()}
        if marker == "list":
            value = payload.get("value", [])
            return [self._deserialize_value(v) for v in value]
        return payload

    def _persist_to_disk(self, key: str, value: Any) -> None:
        try:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            payload_path = self._payload_path(key)
            tmp_path = payload_path.with_suffix(".tmp")
            payload = {
                "key": key,
                "timestamp": time.time(),
                "value": self._serialize_value(value),
            }
            with self.file_lock:
                with open(tmp_path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle)
                    handle.flush()
                    import os

                    os.fsync(handle.fileno())
                tmp_path.replace(payload_path)
        except Exception:
            # Persistence should never take down the pipeline.
            return

    def _load_from_disk(self, key: str) -> Any:
        payload_path = self._payload_path(key)
        if not payload_path.exists():
            return None
        with self.file_lock:
            try:
                raw = json.loads(payload_path.read_text(encoding="utf-8"))
            except Exception:
                return None
        stamp = raw.get("timestamp")
        if not stamp:
            return None
        if time.time() - float(stamp) > self.ttl:
            try:
                payload_path.unlink()
            except Exception:
                pass
            return None
        value = self._deserialize_value(raw.get("value"))
        with self.lock:
            self.memory[key] = value
            self.timestamps[key] = float(stamp)
        return value
