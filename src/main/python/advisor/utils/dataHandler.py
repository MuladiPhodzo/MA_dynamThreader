import os
from pathlib import Path
import sys
import csv
import json
import logging
import threading
import time
import matplotlib.pyplot as plt

from advisor.utils.cache_handler import CacheManager
import pandas as pd
import datetime
from typing import Any, Dict, Optional

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

logger = logging.getLogger("Data_store")

class DataHandler:

    def __init__(self, symbol: str, strategy: str, cache: CacheManager, max_bars: int = 3000):

        self.symbol = symbol
        self.strategy = strategy
        self.cache = cache
        self.max_bars = max_bars

        self.data: Dict[str, pd.DataFrame] = {}
        self.all_timestamps: set = set()

        self.lock = threading.RLock()

        self.base_dir = Path("data") / strategy / symbol
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # start background workers
        self.fetch_thread = threading.Thread(
            target=self._fetch_loop,
            daemon=True
        )

        self.save_thread = threading.Thread(
            target=self._auto_save,
            daemon=True
        )

        self.fetch_thread.start()
        self.save_thread.start()

    def update(self, tf: str, df: pd.DataFrame):
        if df is None or df.empty:
            return

        df = self._sanitize(df)

        with self.lock:

            existing = self.data.get(tf)

            if existing is None:
                self.data[tf] = self._trim(df)
                self.all_timestamps.update(df.index)
                return

            new_rows = df.loc[~df.index.isin(existing.index)]

            if new_rows.empty:
                return

            combined = pd.concat([existing, new_rows])
            combined = self._sanitize(combined)

            self.data[tf] = self._trim(combined)

            self.all_timestamps.update(new_rows.index)

    def add_trade(self, data: pd.DataFrame):
        self.trades.add(pd.DataFrame(data))

    def set_data(self, data: dict):
        for tf, df in data.items():
            self.update(tf, df)

    def set_atomic(self, symbol: str, data: Any) -> None:
        """
        Atomically write symbol data.
        """
        with self.process_lock:
            with self.thread_lock:
                try:
                    data_path = self._data_path(symbol)
                    meta_path = self._meta_path(symbol)

                    tmp_data = data_path.with_suffix(".tmp")
                    tmp_meta = meta_path.with_suffix(".tmp")

                    payload = {
                        "symbol": symbol,
                        "timestamp": time.time(),
                        "data": data
                    }

                    meta = {
                        "symbol": symbol,
                        "updated_at": time.time(),
                        "size": len(json.dumps(payload))
                    }

                    # write tmp files
                    with open(tmp_data, "w", encoding="utf-8") as f:
                        json.dump(payload, f)
                        f.flush()
                        os.fsync(f.fileno())

                    with open(tmp_meta, "w", encoding="utf-8") as f:
                        json.dump(meta, f)
                        f.flush()
                        os.fsync(f.fileno())

                    # atomic replace
                    os.replace(tmp_data, data_path)
                    os.replace(tmp_meta, meta_path)

                except Exception:
                    logger.exception(f"Cache write failed for {symbol}")
                    raise

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

    def save_data_toCSVFile(self, data, file_path: Path):
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
        logger.info("✅ Data saved successfully.")

    def exists(self, symbol: str) -> bool:
        return self._data_path(symbol).exists()

    def delete(self, symbol: str) -> None:
        with self.process_lock:
            with self.thread_lock:
                try:
                    sym_dir = self._symbol_dir(symbol)
                    for file in sym_dir.glob("*"):
                        file.unlink()
                    sym_dir.rmdir()
                except FileNotFoundError:
                    pass
                except Exception:
                    logger.exception(f"Failed deleting cache for {symbol}")

    def _fetch_loop(self):
        while True:

            try:

                data = self.cache.get(self.symbol)

                if data:
                    self.set_data(data)

            except Exception:
                logger.exception("Cache fetch failed")

            time.sleep(300)

    def _auto_save(self):
        while True:
            time.sleep(900)
            try:
                for tf, df in self.data.items():

                    path = self.base_dir / f"{tf}.csv"

                    df.tail(1).to_csv(
                        path,
                        mode="a",
                        header=not path.exists()
                    )

            except Exception:
                logger.exception("Auto save failed")

    # ---------------- internal paths ---------------- #
    def _symbol_dir(self, symbol):
        return Path(f"{self.dir_name}_{symbol}")

    class DataPlotter:
        @staticmethod
        def plot_ticks(ticks, title):
            if ticks is None or len(ticks) == 0:
                logger.info("No data to plot.")
                return
            ticks_frame = pd.DataFrame(ticks)
            ticks_frame['time'] = pd.to_datetime(ticks_frame['time'], unit='s')
            plt.plot(ticks_frame['time'], ticks_frame['ask'], 'r-', label='ask')
            plt.plot(ticks_frame['time'], ticks_frame['bid'], 'b-', label='bid')
            plt.legend(loc='upper left')
            plt.title(title)
            plt.show()

        @staticmethod
        def plot_rates(rates, title):
            if rates is None or len(rates) == 0:
                logger.info("No data to plot.")
                return
            rates_frame = pd.DataFrame(rates)
            rates_frame['time'] = pd.to_datetime(rates_frame['time'], unit='s')
            plt.plot(rates_frame['time'], rates_frame['close'], label='close')
            plt.title(title)
            plt.legend()
            plt.show()

        @staticmethod
        def plot_charts(rates, fast_period, slow_period):
            if rates is None:
                raise ValueError(
                    "Error: `self.results` is None. Run `backtest_strategy()` before plotting.")

            if 'Crossover' not in rates.columns:
                raise ValueError(
                    "Error: 'Crossover' column is missing in `self.results`. Check data processing.")

            if 'close' not in rates.columns or rates['close'].empty:
                raise ValueError("No Close data available")

            plt.figure(figsize=(12, 6))

            # Plot the close price
            plt.plot(rates.index, rates['close'], label="Close", color='black')

            # Plot the fast and slow moving averages
            plt.plot(rates.index, rates['Fast_MA'],
                     label=f"Fast MA ({fast_period})", color='blue')
            plt.plot(rates.index, rates['Slow_MA'],
                     label=f"Slow MA ({slow_period})", color='red')

            # Plot buy/sell signals
            buy_signals = rates.loc[rates['Entry'] == "Buy"]
            sell_signals = rates.loc[rates['Entry'] == "Sell"]

            plt.plot(buy_signals.index, buy_signals['Fast_MA'], '^',
                     color='green', markersize=12, label="Buy Signal")
            plt.plot(sell_signals.index, sell_signals['Fast_MA'],
                     'v', color='red', markersize=12, label="Sell Signal")

            # Plot SL/TP levels
            for i in buy_signals.index:
                plt.hlines(rates.loc[i, 'SL'], i, i + 5, colors='red',
                           linestyles='dashed', label="SL" if i == buy_signals.index[0] else "")
                plt.hlines(rates.loc[i, 'TP'], i, i + 5, colors='green',
                           linestyles='dashed', label="TP" if i == buy_signals.index[0] else "")

            for i in sell_signals.index:
                plt.hlines(rates.loc[i, 'SL'], i, i + 5,
                           colors='red', linestyles='dashed')
                plt.hlines(rates.loc[i, 'TP'], i, i + 5,
                           colors='green', linestyles='dashed')

            plt.title('Moving Average Crossover Signals with SL/TP')
            plt.legend(loc='upper left')
            plt.show()
