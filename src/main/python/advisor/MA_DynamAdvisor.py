import time
import sys
import threading
import queue

from advisor.Client import mt5Client as mt5Client
from advisor.MovingAverage import MovingAverage as MA
from advisor.Trade import TradesAlgo as algorithim
from advisor.GUI import userInput as gui
from advisor.Telegram import Messanger


class RunAdvisorBot:
    def __init__(self):
        # Initialize GUI (user input window)
        self.gui = gui.UserGUI()
        self.symbols = None
        self.init = False
        self.symbol_queue = queue.Queue()
        # Initialize MT5 client and Telegram messenger
        self.client = mt5Client.MetaTrader5Client(self.gui.user_data.get("tf", {}))
        self.telegram = Messanger.TelegramMessenger()
        self.telegram.run_bot_async()  # Run telegram bot thread

    # -------------------------
    # Backtesting Logic
    # -------------------------
    def backtest(self, symbols: list):
        self.client.initialize(self.gui.user_data)
        for symbol in symbols:
            data = self.client.get_rates_range(symbol)

            htf_strategy = MA.MovingAverageCrossover(symbol, data=data["HTF"])
            ltf_strategy = MA.MovingAverageCrossover(symbol, data=data["LTF"])

            HTF_data = htf_strategy.calculate_moving_averages(data["HTF"])
            LTF_data = ltf_strategy.calculate_moving_averages(data["LTF"])

            data = {"HTF": HTF_data, "LTF": LTF_data}
            ltf_strategy.run_moving_average_strategy(symbol, data, ltf_strategy)

    # -------------------------
    # Worker Thread Logic
    # -------------------------
    def worker(self):
        while not self.symbol_queue.empty():
            symbol = self.symbol_queue.get()
            try:
                print(f"✅ Thread started for {symbol}...")
                # Pause handling
                if gui.LogWindow.paused:
                    print(f"⏸ {symbol}: Bot paused. Waiting...")
                    while gui.LogWindow.paused and self.gui.should_run:
                        time.sleep(2)
                    if not self.gui.should_run:
                        break
                    print(f"▶ {symbol}: Bot resumed.")
                    
                """ Main trading loop for each symbol """
                while self.init and self.gui.should_run:
                    data = self.client.get_multi_tf_data(symbol)
                    if data is None:
                        print(f"❌ No data for {symbol}. Retrying in 10 seconds...")
                        time.sleep(10)
                        continue

                    if "HTF" not in data or "LTF" not in data:
                        print(f"⚠️ Missing timeframes for {symbol}. Skipping...")
                        break

                    htf_strategy = MA.MovingAverageCrossover(symbol, data=data["HTF"])
                    ltf_strategy = MA.MovingAverageCrossover(symbol, data=data["LTF"])

                    HTF_data = htf_strategy.calculate_moving_averages(data["HTF"])
                    LTF_data = ltf_strategy.calculate_moving_averages(data["LTF"])

                    if "Fast_MA" not in LTF_data.columns or "Slow_MA" not in LTF_data.columns:
                        print(f"❌ Missing MA columns in LTF data for {symbol}")
                        break

                    if HTF_data is None or LTF_data is None:
                        print(f"⚠️ Moving averages not calculated for {symbol}. Retrying...")
                        time.sleep(10)
                        continue

                    htf_latest = HTF_data.iloc[-1]
                    ltf_latest = LTF_data.iloc[-1]
                    current_price = ltf_latest["close"]
                

                    market_Bias = "Bullish" if htf_latest["Fast_MA"] > htf_latest["Slow_MA"] else "Bearish"
                    ltf_Bias = "Buy" if ltf_latest["Fast_MA"] > ltf_latest["Slow_MA"] else "Sell"

                    trade = algorithim.MT5TradingAlgorithm(symbol, self.telegram, self.gui.user_data)
                    trade.run_Trades(market_bias=market_Bias, ltf_Bias=ltf_Bias, latest=ltf_latest, current_price=current_price, THRESHOLD=self.client.THRESHOLD, symbol=symbol )
                    print(f"🛌 {symbol}: Sleeping for 15 minutes...")
                    time.sleep(900)

            except Exception as e:
                print(f"❌ Exception in thread for {symbol}: {e}")

            finally:
                self.symbol_queue.task_done()
                print(f"❌ Thread for {symbol} has ended.")

    # -------------------------
    # Main Bot Start Logic
    # -------------------------
    def start_bot_logic(self):
        tempClient = mt5Client.MetaTrader5Client()
        res = tempClient.initialize(self.gui.user_data)

        if not res[0]:
            print("❌ Failed to initialize MetaTrader5. Exiting...")
            sys.exit(1)

        self.symbols = res[1]
        tempClient.close()
        print("💹 MarketWatch symbols:", self.symbols)

        # Add symbols to queue
        for sym in self.symbols:
            self.symbol_queue.put(sym)

        if not self.client.logIn(self.gui.user_data):
            print("❌ MT5 login failed. Check credentials.")
            return

        self.init = True
        print("🏃‍♂️ Starting worker threads...")

        threads = []
        for _ in range(len(self.symbols)):
            t = threading.Thread(target=self.worker, daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        print("✅ All threads completed.")
        self.client.close()

    # -------------------------
    # GUI Event Loop
    # -------------------------
    def run(self):
        """Entry point for running the bot with GUI monitoring."""
        def check_gui_closed():
            if self.gui.should_run:
                print("🟢 Running bot...")
                threading.Thread(target=self.start_bot_logic, daemon=True).start()
            else:
                # recheck every 1 second
                self.gui.root.after(1000, check_gui_closed)

        check_gui_closed()
        self.gui.root.mainloop()


# -------------------------
# Run Entry
# -------------------------
if __name__ == "__main__":
    bot = RunAdvisorBot()
    bot.run()
