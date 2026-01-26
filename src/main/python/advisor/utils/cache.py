import json
import threading
import time
from pathlib import Path
import logging
import sys

from advisor.utils.locks import CACHE_LOCK, THREAD_LOCK
# -------------------------
# Logging Configuration
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("MA_DynamAdvisor.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)


class CacheManager:
    def __init__(self, cache_file="cache.json", ttl=60):

        self.cache_file = Path(cache_file)
        self.ttl = ttl
        self.lock = THREAD_LOCK
        self.memory = {}
        self.timestamps = {}
        self.cache_lock = CACHE_LOCK
        self.load_cache()

        # background serializer
        self.auto_save_thread = threading.Thread(target=self._auto_save, daemon=True)
        self.auto_save_thread.start()

    # Store any value
    def set(self, key, value):
        with self.lock:
            self.memory[key] = value
            self.timestamps[key] = time.time()

    # Retrieve and check TTL
    def get(self, key) -> dict:
        with self.lock:
            if key not in self.memory:
                return None

            if time.time() - self.timestamps.get(key, 0) > self.ttl:
                # expired
                del self.memory[key]
                return None

            return self.memory[key]

    def get_by_group(self, group_val: str):
        with self.lock:
            cache = {}
            for key, data in self.memory.items():
                if group_val in key:
                    if time.time() - self.timestamps.get(key, 0) > self.ttl:
                        # expired
                        del self.memory[key]
                    else:
                        cache[key] = data
            return cache

    def snapshot(self, ts):
        """Return multi-TF snapshot at timestamp"""
        snap = {}
        for tf, df in self.memory.items():
            if ts in df.index:
                snap[tf] = df.loc[ts]
        return snap

    # Save to disk
    def save_cache(self):
        with self.lock:
            data = {"memory": self.memory, "timestamps": self.timestamps}
            self.cache_file.write_text(json.dumps(data, indent=4))

    def load_cache(self):
        if not self.cache_file.exists():
            return
        try:
            data = json.loads(self.cache_file.read_text())
            self.memory = data.get("memory", {})
            self.timestamps = data.get("timestamps", {})
        except Exception as e:
            logger.info(f"❌ Error loading cache: {e}")
            pass

    # Saves every 5 minutes
    def _auto_save(self):
        while True:
            time.sleep(60)
            self.save_cache()
