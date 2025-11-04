import time
import sys
import os
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
        self.gui.set_stop_callback(self.stop_bot) 
        self.symbols = None
        self.init = False
        self.symbol_queue = queue.Queue()
        self.stop_event = threading.Event()
        
        
        self.client = mt5Client.MetaTrader5Client(self.gui.user_data.get("tf", {})) # Initialize MT5 client and Telegram messenger
        self.telegram = Messanger.TelegramMessenger()
        self.telegram.set_stop_callback(self.stop_bot)
        self.telegram.run_bot_async()  # Run telegram bot thread
        
        self.gui.root.protocol("WM_DELETE_WINDOW", self.on_close) # Handle GUI close event
    
    def stop_bot(self):
        """Cleanly stop all threads and MT5 session."""
        print("🛑 Stopping Advisor Bot...")

        # Stop GUI loop if it's running
        self.gui.should_run = False
        self.gui.status.config(text="🛑 Bot Stopped", state='disabled')
        
        # Signal threads to stop
        self.stop_event.set()
        try:
            self.client.close()
            print("📴 MT5 connection closed.")
        except Exception as e:
            print(f"⚠️ Error closing MT5: {e}")
        try:
            self.telegram.send_message("🛑 Advisor Bot has been stopped manually.")
        except Exception:
            pass

        print("✅ Bot stopped successfully.")

            
    def on_close(self):
        """Triggered when the GUI window is closed."""
        print("❌ Closing GUI and stopping bot...")
        self.stop_bot()
        self.gui.root.destroy()
        sys.exit(0)

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
        while not self.symbol_queue.empty() and not self.stop_event.is_set():
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
                while self.init and self.stop_event.is_set():
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
                    for _ in range(900):
                        if self.stop_event.is_set():
                            break
                        time.sleep(1)

            except Exception as e:
                print(f"❌ Exception in thread for {symbol}: {e}")

            finally:
                self.symbol_queue.task_done()
                print(f"❌ Thread for {symbol} has ended.")

    # -------------------------
    # Main Bot Start Logic
    # -------------------------
    def start_bot_logic(self):
        try:           
            print("🔄 Initializing MetaTrader5 connection...")
            self.res = self.client.logIn(self.gui.user_data)

            if not self.res[0]:
                raise Exception("Failed to initialize MetaTrader5. Check Network Connection or Credentials. Exiting...")

            self.symbols = self.res[1]
            print("💹 MarketWatch symbols:", self.symbols)
            
            print("🚀 MetaTrader5 initialization complete.")
            # Add symbols to queue
            for sym in self.symbols:
                self.symbol_queue.put(sym)
                
        except Exception as e:
            print(f"❌ Exception during MT5 initialization: {e}")
            self.gui.pop_up_error(f"MetaTrader5 initialization failed: {e}")
            self.client.close()
            sys.exit(1)
            
        finally:
            print("🚀 MetaTrader5 initialization complete.")
            self.init = True
            print("🏃‍♂️ Starting worker threads...")

            threads = []
            for _ in range(len(self.symbols)):
                t = threading.Thread(target=self.worker, daemon=True)
                t.start()
                threads.append(t)

            # Wait for threads to finish or stop event
            while any(t.is_alive() for t in threads):
                if self.stop_event.is_set():
                    print("🧹 Waiting for threads to finish...")
                    break
                time.sleep(1)
                
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
    
    LOCK_FILE = os.path.splitext(os.path.basename(sys.argv[0]))[0] + ".lock"
    bot : RunAdvisorBot
    
    def insure_single_instance(self):
        """Ensure only one instance of the bot is running using a lock file."""
        res = False
        lock_file = os.path.splitext(os.path.basename(sys.argv[0]))[0] + ".lock"
        if os.path.exists(lock_file):
            bot.gui.pop_up_error("⚠️ Another instance of MA_DynamAdvisor is already running. Exiting...")
            res = bot.gui.prompt_window("Delete All instances of the bot before starting a new one.")

        return res
    
    try:
        # Check if another instance is running
        if insure_single_instance():
            os.remove(LOCK_FILE)
            print("✅ Previous lock file removed. Continuing...") 

        # Create lock file
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))

        # Start the bot
        bot = RunAdvisorBot()
        bot.run()

    except KeyboardInterrupt:
        print("\n🟥 Bot stopped manually.")

    except Exception as e:
        print(f"❌ Error: {e}")

    finally:
        # Always remove lock file on exit
        if os.path.exists(LOCK_FILE):
            try:
                os.remove(LOCK_FILE)
                print("✅ Lock file removed. Bot exited cleanly.")
            except Exception as e:
                print(f"⚠️ Could not remove lock file: {e}")

    
