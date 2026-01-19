# -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# MA_DynamAdvisor Bot Main Module
# -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
import asyncio
import os
import sys
import time
import datetime
import threading
import queue
import logging
import signal

from advisor.utils.ThreadHandler import ThreadHandler
from advisor.utils import dataHandler as utils
from advisor.utils.cache import CacheManager as cache

from advisor.Client import mt5Client
from advisor.MovingAverage import MovingAverage as MA
from advisor.Trade import TradesAlgo as algorithim
from advisor.GUI import userInput as gui
from advisor.Telegram.runner import run as TelegramRunner


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


class RunAdvisorBot:
    """Main Advisor Bot Orchestrator (integrated with ThreadHandler)"""

    SLEEP_INTERVAL = 15 * 60  # 15 minutes (configurable)
    RETRY_DELAY = 10  # Delay before retry on data fetch errors

    def __init__(self):
        # Initialize GUI
        self.next_cycle = datetime.datetime().now().date()
        self.backtest_cycle = True
        self.gui = gui.UserGUI()
        self.gui.set_stop_callback(self.stop_bot)

        self.stop_callback = None
        self.symbol_queue = queue.Queue()  # kept for compatibility but not required now
        self.stop_event = threading.Event()   # global stop
        self.paused_event = threading.Event()
        self.init = False
        # Thread handler
        self.thread_handler = ThreadHandler(logger=logger.info)
        self.cache = cache()
        self.data_handler = None

        # Initialize MT5 client
        self.client = mt5Client.MetaTrader5Client()
        self.symbols = self.client.symbols
        # Start Telegram runner as managed thread
        self.thread_handler.start_thread(
            name="telegram_runner",
            group="system",
            ttype="telegram",
            target=self._telegram_runner_wrapper,
            args=(),
            auto_restart=True,
            max_restarts=3,
            callbacks={
                "on_start": lambda t: logger.info("📨 Telegram runner started."),
                "on_stop": lambda t: logger.info("📨 Telegram runner stopped."),
                "on_error": lambda t: logger.warning("📨 Telegram runner crashed."),
            },
        )

        # Ensure GUI close event calls our on_close.
        self.gui.root.wm_protocol("WM_DELETE_WINDOW", self.on_close)

    # -------------------------------------------------------------------------
    # Telegram wrapper (for ManagedThread)
    # -------------------------------------------------------------------------
    def _telegram_runner_wrapper(self, stop_event, pause_event):
        """Run the async Telegram runner. Exits when runner exits or stop_event set."""
        try:
            # Run the Telegram coroutine until it completes or stop_event is set.
            asyncio.run(TelegramRunner())
        except Exception as e:
            logger.exception(f"Telegram runner error in main: {e}")
            # let ManagedThread handle restarts if enabled

    # -------------------------
    # Bot Control
    # -------------------------
    def set_stop_callback(self, callback):
        """Set a callback function to be called on bot stop."""
        self.stop_callback = callback

    def _stop_gui(self):
        """Safely stop GUI loop if running."""
        try:
            if getattr(self.gui, "root", None):
                try:
                    self.gui.should_run = False
                    try:
                        self.gui.root.destroy()
                    except Exception:
                        pass
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"⚠ GUI stop issue: {e}")

    def _close_client(self):
        """Safely close MT5 client."""
        try:
            if getattr(self.client, "close", None):
                self.client.close()
                logger.info("📴 MT5 connection closed.")
        except Exception as e:
            logger.warning(f"⚠ Error closing MT5: {e}")

    def _call_stop_callback(self):
        """Call external stop callback."""
        try:
            if self.stop_callback:
                logger.info("🧩 Running external stop callback...")
                self.stop_callback()
        except Exception as e:
            logger.warning(f"⚠ stop_callback raised: {e}")

    def stop_bot(self):
        """Cleanly stop all threads and MT5 session."""
        if self.stop_event.is_set():
            logger.info("✅ stop_bot called but stop_event already set.")
            return

        logger.info("🛑 Stopping Advisor Bot...")
        self.stop_event.set()

        # Stop managed threads
        try:
            self.thread_handler.stop_all()
            # Also stop any threads using the legacy symbol_queue approach (if used)
        except Exception as e:
            logger.warning(f"⚠ Error stopping thread handler: {e}")

        self._stop_gui()
        self._close_client()
        self._call_stop_callback()

        # Wait for threads to finish gracefully (short timeout)
        try:
            self.thread_handler.wait_for_all(timeout=10)
        except Exception as e:
            logger.warning(f"⚠ Error waiting for threads: {e}")

        logger.info("✅ Bot stop sequence completed.")

    def on_close(self):
        """Triggered when the GUI window is closed."""
        logger.info("❌ Closing GUI and stopping bot...")
        try:
            self.client.close()
        except Exception:
            pass
        finally:
            self.stop_bot()

    # -------------------------
    # Backtesting Logic
    # -------------------------
    def backtest_all_symbols(self, symbols: list) -> dict:
        """
        Backtest all symbols and return performance registry.
        """

        results = {}

        for symbol in symbols:
            try:
                data = self.client.get_multi_tf_data(symbol)
                strategy = MA.MovingAverageCrossover(symbol, data)
                performance = strategy.run_MA_Strategy(data, backtest=self.client.backtest)
                self.cache.set(symbol, performance)
                results[symbol] = performance
            except Exception as e:
                logger.warning(f"Backtest failed for {symbol}: {e}")
        return results

    def select_best_symbols(self, results: dict, min_win_rate: float = 78.0) -> list:
        """
        Select best-performing symbols based on backtest metrics.
        """
        return [
            symbol
            for symbol, stats in results.items()
            if stats.get("win_rate", 0) >= min_win_rate
        ]

    def run_backtest_cycle(self):
        """
        Run quarterly backtest and update active symbols.
        """

        now = datetime.datetime.now()

        if not self.client.backtest:
            return

        if now == self.next_cycle:
            return

        logger.info(f"Starting scheduled backtest cycle. date now: {now}, next cycle: {self.next_cycle}")

        results = self.backtest_all_symbols(self.client.symbols)
        best_symbols = self.select_best_symbols(results)

        # Update registry
        for sym, stats in results.items():
            self.cache.set(sym, stats)

        # Activate only best symbols
        self.client.symbols = best_symbols

        # Schedule next cycle
        self.next_cycle = now + datetime.timedelta(days=90)
        self.client.backtest = False
        logger.info(f"Backtest cycle completed. Next cycle scheduled for {self.next_cycle}.")

    # -------------------------
    # Worker Helpers
    # -------------------------
    def _wait_if_paused(self, symbol):
        """Pause handling logic (keeps original GUI pause semantics)."""
        if gui.LogWindow.paused:
            logger.info(f"⏸ {symbol}: Bot paused. Waiting...")
            while gui.LogWindow.paused and self.gui.should_run and not self.stop_event.is_set():
                time.sleep(2)
            if not self.gui.should_run or self.stop_event.is_set():
                logger.info(f"🛑 {symbol}: Bot stopped during pause.")
                raise SystemExit
            logger.info(f"▶ {symbol}: Bot resumed.")

    # need to be reconfigured
    def _get_symbol_data(self, symbol) -> bool:
        """Fetch multi-timeframe data for the given symbol."""
        try:
            res = {}
            if getattr(self.client.cache, "memory", not {}):
                res = self.data_handler.set_data(self.cache.get(symbol))
            else:
                res = self.client.get_multi_tf_data(symbol, self.data_handler)
            if not isinstance(self.data_handler.data, dict) or len(self.data_handler.data) == 0:
                logger.warning(f"⚠️ Invalid multi-timeframe data received for {symbol}")
                return res

            # Ensure all values are proper DataFrames
            for tf, df in self.data_handler.data.items():
                if df is None or getattr(df, "empty", True):
                    logger.warning(f"⚠️ Missing/empty data for {symbol} @ {tf}")
                    return res

            return res

        except Exception as e:
            logger.error(f"❌ Error fetching data for {symbol}: {e}")
            return False

    def _calculate_indicators(self, symbol) -> bool:
        import pandas as pd
        """Calculate MA indicators for every timeframe in the dict."""
        try:
            processed = {}
            strategy = MA.MovingAverageCrossover(symbol, caching=self.client.cache, data_handler=self.data_handler)

            # Calculate MA for ALL timeframes
            res = strategy.calculate_moving_averages_data(self.gui.user_data["sl"])

            if not self.data_handler.data.get("Bias"):
                logger.error("Main Trend missing or empty in data dict")

            # Validate every timeframe has MA columns
            if res:
                for tf, df in self.data_handler.data.items():
                    if tf == "Main_Trend":
                        continue
                    if df is None or not isinstance(df, pd.DataFrame):
                        logger.error(f"❌ Missing data after MA calc for {symbol} @ {tf}")
                        return res

                    if not all(col in df.columns for col in ["Fast_MA", "Slow_MA"]):
                        logger.error(f"❌ Missing MA columns for {symbol} @ {tf}")
                        return res

                    processed[tf] = df
                    self.data_handler.update(tf, df)
                return res
            return res

        except Exception as e:
            logger.exception(f"❌ Error calculating indicators for {symbol}: {e}")
            return False

    def _analyze_and_trade(self, symbol, data_dict):
        """Use lowest TF for entries and highest TF for bias."""
        # Execute trade
        self.trade.run_trades(
            THRESHOLD=self.client.THRESHOLD,
            symbol=symbol,
        )

    def _sleep_with_stop_check(self, duration):
        """Sleep while periodically checking if stop_event is set. Returns True if full sleep completed."""
        return not self.stop_event.wait(timeout=duration)

    # -------------------------
    # Per-symbol thread wrapper (for ManagedThread)
    # -------------------------
    def _process_symbol_thread(self, symbol, stop_event, pause_event):
        """
        This wrapper runs the original _process_symbol logic but accepts
        the managed stop_event and pause_event so threads can be individually controlled.
        It will honor either the global self.stop_event or the per-thread stop_event.
        """
        try:
            # Create the trading algorithm instance
            self.data_handler = utils.dataHandler()
            self.trade = algorithim.MT5TradingAlgorithm(symbol, self.thread_handler.get_by_name("telegram_runner"), self.gui.user_data)

            # Keep running while initialization flag is set and neither global nor local stop requested
            while getattr(self, "init", False) and not (self.stop_event.is_set() or stop_event.is_set()):
                # First respect GUI-level pause, then per-thread pause_event.
                self._wait_if_paused(symbol)

                # Also allow the pause_event to block
                pause_event.wait()
                if (self.stop_event.is_set() or stop_event.is_set()):
                    break

                res = self._get_symbol_data(symbol)
                if res:
                    # Wait but allow exit if any stop event set
                    logger.warning("data dict is empty")
                    if self.stop_event.wait(timeout=self.RETRY_DELAY) or stop_event.wait(timeout=0):
                        break
                    continue

                # calculate indicators
                processed = self._calculate_indicators(symbol)
                if processed:
                    if self.stop_event.wait(timeout=self.RETRY_DELAY):
                        break
                    continue

                self._analyze_and_trade(symbol, self.data_handler.data)
                logger.info(f"🛌 Next {symbol} cycle in {self.SLEEP_INTERVAL // 60} minutes...")
                if not self._sleep_with_stop_check(self.SLEEP_INTERVAL):
                    break

        except SystemExit:
            logger.warning(f"🧵 {symbol}: SystemExit received, ending worker loop.")
        except Exception as e:
            logger.exception(f"❌ Error processing symbol {symbol}: {e}")

    # -------------------------
    # MT5 initialize + start managed worker threads
    # -------------------------
    def _mt5_initialize(self):
        """Initialize MT5 connection and populate the symbols list. Returns True on success."""
        logger.info("🔄 Initializing MetaTrader5 connection...")
        try:
            if not self.client.logIn(self.gui.user_data):
                raise ConnectionError("Failed to initialize MetaTrader5. Check credentials or network.")
            return True

        except Exception as e:
            logger.exception(f"❌ MT5 initialization failed: {e}")
            try:
                self.gui.pop_up_error(f"MetaTrader5 initialization failed: {e}")
            except Exception:
                pass
            try:
                self.client.close()
            except Exception:
                pass
            self.stop_event.set()
            return False

    def _start_managed_workers(self):
        """Create a managed thread per symbol via ThreadHandler."""
        self.init = True
        logger.info("🏃‍♂️ Starting managed worker threads...")

        for sym in self.symbols:
            name = f"worker_{sym}"
            self.thread_handler.start_thread(
                name=name,
                group="symbol",
                ttype="worker",
                target=self._process_symbol_thread,
                args=(sym,),
                auto_restart=True,   # you can toggle per-symbol auto-restart
                max_restarts=2,
                callbacks={
                    "on_start": lambda t, s=sym: logger.info(f"✅ Worker for {s} started."),
                    "on_stop": lambda t, s=sym: logger.info(f"🧵 Worker for {s} stopped."),
                    "on_error": lambda t, s=sym: logger.warning(f"❌ Worker for {s} crashed."),
                },
            )
            time.sleep(0.2)

    def _wait_for_managed_workers(self):
        """Wait for managed threads to finish (until global stop_event)."""
        try:
            # Block until all worker threads are no longer alive or global stop_event
            while any(t.thread.is_alive() and t.type == "worker" for t in self.thread_handler.threads.values()) and not self.stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("🟥 KeyboardInterrupt received, stopping workers.")
            self.stop_event.set()

        logger.info("✅ All managed worker threads completed.")
        try:
            self.client.close()
        except Exception:
            pass

    # -------------------------
    # Main Bot Logic
    # -------------------------
    def start_bot_logic(self):
        """Top-level bot start that delegates to smaller helpers."""
        try:
            if not self._mt5_initialize():
                raise ConnectionError("failed mt5 connection")

            self.run_backtest()
            # start bot logic with backtested symbols
            self._start_managed_workers()
            self._wait_for_managed_workers()
        except Exception as e:
            logger.exception(f'Bot raised Exception: {e}')
        except ConnectionError as e:
            logger.warning(f'mt5 connection failed: {e}')
            self.stop_bot()

    # -------------------------
    # GUI Event Loop
    # -------------------------
    def run(self):
        """Entry point for running the bot with GUI monitoring."""
        def start_when_ready():
            if self.gui.should_run:
                logger.info("🟢 Running bot...")
                threading.Thread(target=self.start_bot_logic, daemon=True).start()
            else:
                self.gui.root.after(1000, start_when_ready)

        start_when_ready()
        self.gui.root.mainloop()

# -------------------------
# Single Instance Guard
# -------------------------
def ensure_single_instance(lock_file):
    """Ensure only one instance of the bot is running."""
    if os.path.exists(lock_file):
        logger.warning("⚠️ Another instance of MA_DynamAdvisor is already running.")
        return False
    with open(lock_file, "w") as f:
        f.write(str(os.getpid()))
    return True


if __name__ == "__main__":
    LOCK_FILE = os.path.splitext(os.path.basename(sys.argv[0]))[0] + ".lock"
    bot = None

    try:
        # --- Register OS signals to call logging.shutdown or our stop
        signal.signal(signal.SIGINT, lambda *_: logging.shutdown())
        if sys.platform != "win32":
            signal.signal(signal.SIGTERM, lambda *_: logging.shutdown())
        else:
            logger.info("⚠️ SIGTERM not supported on Windows — using SIGINT only.")

        if not ensure_single_instance(LOCK_FILE):
            logger.info("⚠️ Please close the running instance before starting a new one.")
            sys.exit(1)

        bot = RunAdvisorBot()
        bot.run()

    except KeyboardInterrupt:
        logger.info("🟥 Bot stopped manually.")
    except Exception as e:
        logger.exception(f"❌ Processes stopped with: {e}")
    finally:
        if os.path.exists(LOCK_FILE):
            try:
                os.remove(LOCK_FILE)
                logger.info("✅ Lock file removed. Bot exited cleanly.")
            except Exception as e:
                logger.warning(f"⚠️ Could not remove lock file: {e}")
