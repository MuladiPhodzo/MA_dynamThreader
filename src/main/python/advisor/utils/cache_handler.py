from typing import Dict, Any
import threading
import time


class CacheManager:

    def __init__(self, ttl: int = 180):
        self.ttl = ttl
        self.memory: Dict[str, Any] = {}
        self.timestamps: Dict[str, float] = {}

        self.lock = threading.RLock()

    def set(self, key: str, value: Any):

        with self.lock:
            self.memory[key] = value
            self.timestamps[key] = time.time()

    def get(self, key: str):

        with self.lock:

            if key not in self.memory:
                return None

            if time.time() - self.timestamps.get(key, 0) > self.ttl:
                self.memory.pop(key, None)
                self.timestamps.pop(key, None)
                return None

            return self.memory[key]

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