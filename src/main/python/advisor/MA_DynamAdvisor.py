import time
import sys
import threading, queue

from Client import mt5Client as mt5Client
from MovingAverage import MovingAverage as MA
from Trade import TradesAlgo as algorithim
from GUI import userInput as gui
from Telegram import Messanger


class RunAdvisorBot:
    def __init__(self):
        self.symbols = None
        self.init = None
        self.gui = gui.UserGUI()
        # self.gui.root.update()  # Ensure GUI is fully initialized
        self.log_window =  gui.LogWindow(None)
        # sys.stdout = self.log_window.redirector
        # sys.stderr = self.log_window.redirector
        # self.gui.user_data = self.gui.user_data
        self.symbol_queue = queue.Queue()
        self.client = mt5Client.MetaTrader5Client(self.gui.user_data.get('tf', {}))
        self.telegram = Messanger.TelegramMessenger()
        self.telegram.run_bot_async()  # Start the Telegram bot in a separate thread

    def backtest(self, symbols: list):
        self.client.initialize(self.gui.user_data)
        for symbol in symbols:
            data = self.client.get_rates_range(symbol)
            
            htf_strategy = MA.MovingAverageCrossover(symbol, data=data["HTF"])
            ltf_strategy = MA.MovingAverageCrossover(symbol, data=data["LTF"])
            
            HTF_data = htf_strategy.calculate_moving_averages(data["HTF"])
            LTF_data = ltf_strategy.calculate_moving_averages(data["LTF"])
            
            data = {'HTF': HTF_data, 'LTF': LTF_data}
            ltf_strategy.run_moving_average_strategy(symbol, data, ltf_strategy)

    def worker(self):
        while not self.symbol_queue.empty():
            symbol = self.symbol_queue.get()
            try:
                print(f'✅ Thread started for {symbol}...')
                while self.init:
                    data = self.client.get_multi_tf_data(symbol)
                    if data is None:
                        print(f'❌error: No data returned for {symbol}. Retrying in 10 seconds...')
                        time.sleep(10)
                        continue

                    if "HTF" not in data or "LTF" not in data:
                        print(f'⚠️ error :Missing timeframes for {symbol}. Skipping...')
                        break

                    htf_strategy = MA.MovingAverageCrossover(symbol, data=data["HTF"])
                    ltf_strategy = MA.MovingAverageCrossover(symbol, data=data["LTF"])

                    HTF_data = htf_strategy.calculate_moving_averages(data["HTF"])
                    LTF_data = ltf_strategy.calculate_moving_averages(data["LTF"])

                    if "Fast_MA" not in LTF_data.columns or "Slow_MA" not in LTF_data.columns:
                        print(f'❌ Missing MA columns in LTF data for {symbol}')
                        break

                    if HTF_data is None or LTF_data is None:
                        print(f'⚠️ Moving averages not calculated for {symbol}. Skipping...')
                        time.sleep(10)
                        continue

                    htf_latest = HTF_data.iloc[-1]
                    ltf_latest = LTF_data.iloc[-1]
                    current_price = ltf_latest['close']

                    market_Bias = "Bullish" if htf_latest['Fast_MA'] > htf_latest['Slow_MA'] else "Bearish"
                    ltf_Bias = "Buy" if ltf_latest["Fast_MA"] > ltf_latest['Slow_MA'] else "Sell"

                    trade = algorithim.MT5TradingAlgorithm(symbol, self.telegram, self.gui.user_data)
                    trade.run_Trades(market_Bias, ltf_Bias, ltf_latest, current_price, self.client.THRESHOLD, symbol)

                    print(f'🛌 {symbol} Thread sleeping for 15 minutes....')
                    time.sleep(900)  # 15 minutes

            except Exception as e:
                print(f'❌ Exception in thread {symbol}: {e}')

            finally:
                self.symbol_queue.task_done()
                print(f'❌ Thread for {symbol} has ended.')

    def start_bot_logic(self):
        tempClient = mt5Client.MetaTrader5Client()
    
        res = tempClient.initialize(self.gui.user_data)
        if not res[0]:
            print('❌ Failed to initialize MetaTrader5. Exiting...')
            sys.exit(1)

        self.symbols = res[1]
        tempClient.shutdown()
        print('Marketwatch symbols:', self.symbols)

        # add symbols to que
        for sym in self.symbols:
            self.symbol_queue.put(sym)

        if not self.client.logIn(self.gui.user_data):
            print('❌ Login failed.')
            return
        self.init = True

        print('🏃‍♂️ Running worker threads...')
        threads = []
        for _ in range(len(self.symbols)):  # Max workers == Max available symbols
            t = threading.Thread(target=self.worker)
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        print("✅ All threads completed.")
        self.client.shutdown()


if __name__ == "__main__":
    bot = RunAdvisorBot()
    # bot.backtest(bot.symbols)
    def check_gui_closed():
        if bot.gui.should_run:
            print('🟢running bot......')
            threading.Thread(target=bot.start_bot_logic).start()
        else:
            bot.gui.root.after(1000, check_gui_closed)
            
    check_gui_closed()
    bot.gui.root.mainloop()
