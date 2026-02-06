import os
import sys
import csv
import json
import logging
import threading
import time
from pathlib import Path

from advisor.utils.locks import CACHE_LOCK, THREAD_LOCK
import pandas as pd
import datetime
from typing import Dict, Optional

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
    def __init__(self, ttl=60):
        self.ttl = ttl
        self.lock = THREAD_LOCK
        self.memory = {}
        self.timestamps = {}
        self.cache_lock = CACHE_LOCK

        # background serializer
        self.auto_save_thread = threading.Thread(target=self._auto_save, daemon=True)
        self.auto_save_thread.start()

    # Store any value
    def set(self, key, value):
        with self.lock:
            self.memory[key] = value
            self.timestamps[key] = time.time()

    def set_atomic(self, key, value):
        with self.cache_lock:
            tmp_key = f"{key}.__tmp__"
            self.set(tmp_key, value)
            self.rename(tmp_key, key)

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

class dataHandler:
    def __init__(self, symbol, strategy: str, max_bars=3000):
        self.symbol = symbol
        self.dir_name = os.path.dirname(f"{self.symbol}_{strategy}")
        
        self.cache_handler = CacheManager()
        self.data : Dict[str, pd.DataFrame] = self.cache_handler.get(symbol)
        self.all_timestamps = set(
            self.all_timestamps.update(df)
            for tf, df in self.data.items())
        
        self.trades = pd.DataFrame()
        self.max_bars = max_bars
        self.FILE_LOCK = 

    def get_cache(self, key):
        return self.cache_handler.get(key)

    def update(self, tf: str, df: pd.DataFrame):
        """
        Append new rows into timeframe data instead of replacing.
        Deduplicates by index, sanitizes, trims, and updates timestamps.
        """
        if df is None or df.empty:
            return

        df = self._sanitize(df)

        # If timeframe does not exist yet → set directly
        if tf not in self.data or self.data[tf] is None:
            self.data[tf] = self._trim(df)
            self.update_timestamps(df)
            return

        existing = self.data[tf]

        # Append only NEW rows (index-based)
        new_rows = df.loc[~df.index.isin(existing.index)]

        if new_rows.empty:
            return

        # Concatenate + sanitize again (cheap & safe)
        combined = pd.concat([existing, new_rows])
        combined = self._sanitize(combined)
        combined = self._trim(combined)

        self.data[tf] = combined
        self.update_timestamps(new_rows)

    def add_trade(self, data: pd.DataFrame):
        self.trades.add(pd.DataFrame(data))

    def set_data(self, data: dict):
        for tf, df in data.items():
            self.update(tf, df)

    def get(self, tf: str) -> Optional[pd.DataFrame]:
        return self.data.get(tf)

    def get_all(self) -> Dict[str, pd.DataFrame]:
        return self.data

    def update_timestamps(self, df: pd.DataFrame):
        if isinstance(df, pd.DataFrame) and "Slow_MA" in df.columns:
            self.all_timestamps.update(df.index)

    def get_all_timestamps(self) -> set:
        return sorted(self.all_timestamps) if self.all_timestamps else None

    def snapshot(self, timestamp):
        """Return multi-TF snapshot at timestamp"""
        snap = {}
        for tf, df in self.data.items():
            if timestamp in df.index:
                snap[tf] = df.loc[timestamp]
        return snap

    def common_timestamps(self):
        return sorted(set.intersection(*self.all_timestamps)) if self.all_timestamps else []

    def persist_tail(self, tf, path, rows=1):
        df = self.data.get(tf)
        if df is None:
            return
        df.tail(rows).to_csv(
            path,
            mode="a",
            header=not os.path.exists(path)
        )

    # ---------------- internal ---------------- #
    def _trim(self, df: pd.DataFrame):
        if len(df) > self.max_bars:
            return df.iloc[-self.max_bars:].copy()
        return df

    def _sanitize(self, df: pd.DataFrame):
        df = df[~df.index.duplicated(keep="last")]
        df.sort_index(inplace=True)
        return df

        # Save to disk
    def save_cache(self):
        with self.lock:
            data = {"memory": self.memory, "timestamps": self.timestamps}
            self.cache_file.write_text(json.dumps(data, indent=4))

    def load_cache(self):
        if not self.file.exists():
            return
        try:
            data = json.loads(self.cache_file.read_text())
            self.memory = data.get("memory", {})
            self.timestamps = data.get("timestamps", {})
        except Exception as e:
            logger.info(f"❌ Error loading cache: {e}")
            pass

    def save_trade(self, trade_data, file_type="json"):
        """
        Saves trade information to a file (JSON or CSV).

        Args:
            trade_data (dict): Trade details to save.
            file_type (str): 'json' or 'csv'.
        """
        def _toJSON(self, file_path, trade_data, timestamp):
            """Helper method for JSON saving"""
            entry = {"timestamp": timestamp, **trade_data}

            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except json.JSONDecodeError:
                    data = []
            else:
                data = []

            data.append(entry)

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

            logger.info(f"✅ Trade saved to {file_path}")

        def _toCSV(file_path, trade_data):
            import datetime
            """Helper method for CSV saving"""
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            entry = {"timestamp": timestamp, **trade_data}
            file_exists = os.path.exists(file_path)

            with open(file_path, "a", newline='', encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=entry.keys())
                if not file_exists:
                    writer.writeheader()
                writer.writerow(entry)
            logger.info(f"✅ Trade saved to {file_path}")

        # Ensure trades directory exists
        os.makedirs("trades", exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        with self.file_lock:
            if file_type.lower() == "json":
                file_path = "trades/trades_log.json"
                _toJSON(file_path, trade_data, timestamp)

            elif file_type.lower() == "csv":
                file_path = "trades/trades_log.csv"
                _toCSV(file_path, trade_data, timestamp)
            else:
                raise ValueError("Unsupported file type. Use 'json' or 'csv'.")

    def save_data_toCSVFile(self, data, file_path):
        import tabulate
        """
        Save data to a CSV + formatted TXT table.
        - Creates directories if missing.
        - Writes CSV safely (create or append).
        - Writes a formatted pretty table using 'tabulate'.
        """

        # Ensure data is not empty
        if data is None or len(data) == 0:
            logger.warning(f"No data to write for file {file_path}. Skipping.")
            return

        df = pd.DataFrame(data)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        # Auto-create folder if it doesn't exist
        os.makedirs(self.dir_name, exist_ok=True)

        with self.file_lock:
            with self.thread_lock:
                file_exists = os.path.exists(file_path)

                # ============================
                # 1️⃣ SAVE RAW CSV
                # ============================
                if file_exists:
                    logger.info(f"Appending to CSV: {file_path}")
                    df.to_csv(file_path, index=False, mode='a', header=False)
                else:
                    logger.info(f"Creating CSV: {file_path}")
                    df.to_csv(file_path, index=False, mode='w', header=True)

                # ============================
                # 2️⃣ SAVE PRETTY TABLE (TXT)
                # ============================

                pretty_text = tabulate.tabulate(df, headers='keys', tablefmt='pretty', showindex=False)

                table_path = file_path.replace(".csv", "_pretty.txt")

                with open(table_path, "a", encoding="utf-8") as f:
                    f.write(f"Timestamp: {timestamp}\n")
                    f.write(pretty_text + "\n\n")

        logger.info(f"📄 Pretty table saved to {table_path}")
        logger.info("✅ Data saved successfully to both CSV + Pretty Table.")

    # Saves every 5 minutes
    def _auto_save(self):
        while True:
            time.sleep(60 * 15)
            self.save_data_toCSVFile()
