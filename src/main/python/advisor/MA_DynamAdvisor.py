# -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# MA_DynamAdvisor Bot Main Module
# -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
import asyncio
import os
import sys
import time
import threading
import queue
import logging
import signal

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
    """Main Advisor Bot Orchestrator"""

    SLEEP_INTERVAL = 15 * 60  # 15 minutes (configurable)
    RETRY_DELAY = 10  # Delay before retry on data fetch errors

    def __init__(self):
        # Initialize GUI 
        self.gui = gui.UserGUI()
        self.gui.set_stop_callback(self.stop_bot)

        self.symbols = []
        self.stop_callback = None
        self.symbol_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.paused_event = threading.Event() 
        self.init = False

        # Initialize MT5 client 
        self.client = mt5Client.MetaTrader5Client(self.gui.user_data.get("tf", {}))

        # Start Telegram runner in a background thread.
        self.telegram_thread = threading.Thread(target=self._start_telegram, daemon=True)
        self.telegram_thread.start()

        # Ensure GUI close event calls our on_close.
        self.gui.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # -------------------------------------------------------------------------
    # Telegram helper - run runner in a background thread safely
    # -------------------------------------------------------------------------
    def _start_telegram(self):
        """Entrypoint for the Telegram runner coroutine executed in a thread."""
        try:
            asyncio.run(TelegramRunner())
        except Exception as e:
            logger.exception(f"Telegram runner thread exited with error: {e}")

    # -------------------------
    # Bot Control
    # -------------------------
    def set_stop_callback(self, callback):
        """Set a callback function to be called on bot stop."""
        self.stop_callback = callback

    def stop_bot(self):
        """Cleanly stop all threads and MT5 session.

        Safe to call from any thread (will set stop_event and attempt graceful shutdown).
        """
        if self.stop_event.is_set():
            logger.info("✅ stop_bot called but stop_event already set.")
            return

        logger.info("🛑 Stopping Advisor Bot...")
        self.stop_event.set()

        # Stop GUI loop if running
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

        # Close MT5 client if connected
        try:
            if getattr(self.client, "close", None):
                self.client.close()
                logger.info("📴 MT5 connection closed.")
        except Exception as e:
            logger.warning(f"⚠ Error closing MT5: {e}")

        # Call optional external stop callback (synchronous)
        try:
            if self.stop_callback:
                logger.info("🧩 Running external stop callback...")
                self.stop_callback()
        except Exception as e:
            logger.warning(f"⚠ stop_callback raised: {e}")

        logger.info("✅ Bot stop sequence initiated. Waiting for worker threads to finish...")

    def on_close(self):
        """Triggered when the GUI window is closed."""
        logger.info("❌ Closing GUI and stopping bot...")
        # Ensure MT5 closed and threads told to stop.
        try:
            self.client.close()
        except Exception:
            pass
        self.stop_bot()

    # -------------------------
    # Backtesting Logic
    # -------------------------
    def backtest(self, symbols: list):
        """Runs backtest on provided symbols."""
        self.client.initialize(self.gui.user_data)
        for symbol in symbols:
            data = self.client.get_rates_range(symbol)
            htf_strategy = MA.MovingAverageCrossover(symbol, data=data["HTF"])
            ltf_strategy = MA.MovingAverageCrossover(symbol, data=data["LTF"])

            HTF_data = htf_strategy.calculate_moving_averages(data["HTF"])
            LTF_data = ltf_strategy.calculate_moving_averages(data["LTF"])

            ltf_strategy.run_moving_average_strategy(
                symbol, {"HTF": HTF_data, "LTF": LTF_data}, ltf_strategy
            )

    # -------------------------
    # Worker Helpers
    # -------------------------
    def _wait_if_paused(self, symbol):
        """Pause handling logic."""
        if gui.LogWindow.paused:
            logger.info(f"⏸ {symbol}: Bot paused. Waiting...")
            while gui.LogWindow.paused and self.gui.should_run and not self.stop_event.is_set():
                time.sleep(2)
            if not self.gui.should_run or self.stop_event.is_set():
                logger.info(f"🛑 {symbol}: Bot stopped during pause.")
                raise SystemExit
            logger.info(f"▶ {symbol}: Bot resumed.")

    def _get_symbol_data(self, symbol):
        """Fetch multi-timeframe data for the given symbol."""
        try:
            data = self.client.get_multi_tf_data(symbol)
            if data is None or "HTF" not in data or "LTF" not in data:
                logger.warning(f"⚠️ Missing or invalid timeframe data for {symbol}. Retrying...")
                return None
            return data
        except Exception as e:
            logger.error(f"❌ Error fetching data for {symbol}: {e}")
            return None

    def _calculate_indicators(self, symbol, data):
        """Calculate moving averages for HTF and LTF data."""
        try:
            htf_strategy = MA.MovingAverageCrossover(symbol, data=data["HTF"])
            ltf_strategy = MA.MovingAverageCrossover(symbol, data=data["LTF"])

            htf_data = htf_strategy.calculate_moving_averages(data["HTF"])
            ltf_data = ltf_strategy.calculate_moving_averages(data["LTF"])

            # Explicit DataFrame emptiness checks (avoid ambiguous truthiness)
            if getattr(ltf_data, "empty", False) or getattr(htf_data, "empty", False):
                logger.error(f"❌ Moving average calculation returned empty data for {symbol}")
                return None, None

            if not all(col in ltf_data.columns for col in ["Fast_MA", "Slow_MA"]):
                logger.error(f"❌ Missing MA columns in LTF data for {symbol}")
                return None, None

            return htf_data, ltf_data

        except Exception as e:
            logger.exception(f"❌ Error calculating indicators for {symbol}: {e}")
            return None, None

    def _analyze_and_trade(self, symbol, htf_data, ltf_data):
        """Determine market bias and execute trade logic."""
        htf_latest = htf_data.iloc[-1]
        ltf_latest = ltf_data.iloc[-1]
        current_price = ltf_latest["close"]

        market_bias = "Bullish" if htf_latest["Fast_MA"] > htf_latest["Slow_MA"] else "Bearish"
        ltf_bias = "Buy" if ltf_latest["Fast_MA"] > ltf_latest["Slow_MA"] else "Sell"

        # NOTE: we pass self.telegram_thread because we don't have a TelegramMessenger object reference
        # If you want to set callbacks on the telegram bot object, change runner.run to return the messenger instance.
        trade = algorithim.MT5TradingAlgorithm(symbol, self.telegram_thread, self.gui.user_data)

        trade.run_Trades(
            market_bias=market_bias,
            ltf_Bias=ltf_bias,
            latest=ltf_latest,
            current_price=current_price,
            THRESHOLD=self.client.THRESHOLD,
            symbol=symbol,
        )

    def _sleep_with_stop_check(self, duration):
        """Sleep while periodically checking if stop_event is set. Returns True if full sleep completed."""
        return not self.stop_event.wait(timeout=duration)

    def _process_symbol(self, symbol):
        """Main trading loop for a single symbol."""
        try:
            while getattr(self, "init", False) and not self.stop_event.is_set():
                self._wait_if_paused(symbol)

                data = self._get_symbol_data(symbol)
                if data is None:
                    time.sleep(self.RETRY_DELAY)
                    continue

                # calculate indicators
                htf_data, ltf_data = self._calculate_indicators(symbol, data)
                if htf_data is None or ltf_data is None:
                    time.sleep(self.RETRY_DELAY)
                    continue

                self._analyze_and_trade(symbol, htf_data, ltf_data)
                logger.info(f"🛌 {symbol}: Sleeping for {self.SLEEP_INTERVAL // 60} minutes...")
                if not self._sleep_with_stop_check(self.SLEEP_INTERVAL):
                    break

        except SystemExit:
            logger.info(f"🧵 {symbol}: SystemExit received, ending worker loop.")
        except Exception as e:
            logger.exception(f"❌ Error processing symbol {symbol}: {e}")

    def worker(self):
        """Worker thread that handles trading logic for each symbol."""
        while not self.symbol_queue.empty() and not self.stop_event.is_set():
            symbol = self.symbol_queue.get()
            try:
                logger.info(f"✅ Thread started for {symbol}...")
                self._process_symbol(symbol)
            except Exception as e:
                logger.exception(f"❌ Exception in thread for {symbol}: {e}")
            finally:
                self.symbol_queue.task_done()
                logger.info(f"🧵 Thread for {symbol} has ended.")

    # -------------------------
    # Main Bot Logic
    # -------------------------
    def start_bot_logic(self):
        try:
            logger.info("🔄 Initializing MetaTrader5 connection...")
            self.res = self.client.logIn(self.gui.user_data)
            if not self.res or not self.res[0]:
                raise ConnectionError("Failed to initialize MetaTrader5. Check credentials or network.")

            self.symbols = self.res[1] or []
            logger.info(f"💹 MarketWatch symbols: {self.symbols}")
            for sym in self.symbols:
                self.symbol_queue.put(sym)

        except Exception as e:
            logger.exception(f"❌ MT5 initialization failed: {e}")
            try:
                self.gui.pop_up_error(f"MetaTrader5 initialization failed: {e}")
            except Exception:
                pass
            self.client.close()
            # Do not sys.exit here; set stop event and return.
            self.stop_event.set()
            return

        # Start workers
        self.init = True
        logger.info("🏃‍♂️ Starting worker threads...")

        threads = []
        for _ in range(len(self.symbols)):
            t = threading.Thread(target=self.worker, daemon=True)
            t.start()
            threads.append(t)

        # Wait for threads to finish or stop event to be set
        try:
            while any(t.is_alive() for t in threads) and not self.stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("🟥 KeyboardInterrupt received, stopping workers.")
            self.stop_event.set()

        logger.info("✅ All threads completed.")
        try:
            self.client.close()
        except Exception:
            pass

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
            print("⚠️ SIGTERM not supported on Windows — using SIGINT only.")

        if not ensure_single_instance(LOCK_FILE):
            print("⚠️ Please close the running instance before starting a new one.")
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