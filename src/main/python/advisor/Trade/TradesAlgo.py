import MetaTrader5 as mt5
import pandas as pd
import datetime as dt
from advisor.Telegram import Messanger

class MT5TradingAlgorithm:
    def __init__(self, symbol, telegram: Messanger.TelegramMessenger, user_data, magic_number=1000):
        """
        Initialize the MT5 trading algorithm.
        :param symbol: The trading symbol (e.g., 'USDJPY').
        :param lot_size: The size of each trade.
        :param magic_number: Unique identifier for this strategy's trades.
        """
        self.TradesData = None
        self.symbol = symbol
        self.lot_size = user_data['volume']
        self.magic_number = magic_number
        self.user_data = user_data
        self.current_position = None  # Track 'buy', 'sell', or None
        self.telegram = telegram

    def place_order(self, action, stop_loss=100, take_profit=300):
        """
        Place a buy or sell order.
        :param action: 'buy' or 'sell'.
        """
        try:
            # Define order type
            order_type = mt5.ORDER_TYPE_BUY if action == 'buy' else mt5.ORDER_TYPE_SELL

            # Get symbol info
            symbol_info = mt5.symbol_info(self.symbol)
            # print(f"Can trade: {symbol_info.trade_mode}") 
            # print(symbol_info._asdict())

            if not symbol_info:
                print(f"Symbol {self.symbol} not be found, cannot place order.")
                return False

            point = symbol_info.point
            price = mt5.symbol_info_tick(self.symbol).ask if action == 'buy' else mt5.symbol_info_tick(self.symbol).bid

            # Prepare order request
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": self.symbol,
                "volume": self.lot_size,
                "type": order_type,
                "price": price,
                "sl": price - stop_loss * point if order_type == mt5.ORDER_TYPE_BUY else price + stop_loss * point,
                "tp": price + take_profit * point if order_type == mt5.ORDER_TYPE_BUY else price - take_profit * point,
                "deviation": 10,
                "magic": self.magic_number,
                "comment": f"{action.capitalize()} trade by Moving Average strategy",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            result = None
            # Send order
            if symbol_info.trade_mode == 0:
                result = mt5.order_send(request)
                if result is None:
                    print("❌ mt5.order_send() returned None — check if MetaTrader is initialized and logged in.")
                    return False
                if result.retcode != mt5.TRADE_RETCODE_DONE:
                    print(f"❌ Order failed: {result.retcode}")
                    return False
                
                else:
                    print(f"✅ {action.capitalize()} order placed at {price}. Retcode: {result.retcode}")
                    self.telegram.send_message(f"🟢Placed {action} {self.symbol} @ {request['price']} | TP: {request['tp']} | SL: {request['sl']}\n NB: use proper risk management")
                
                    self.TradesData.add(request)
                    self.TradesData = self.TradesData.drop(columns=['type_time', 'comment', 'type_filling', 'deviation'])
                    self.current_position = action
                    print(f"🟢 {self.symbol} Placing {action.upper()} order...")
                    return True, request
                    
            else:
                print(f'sending Telegram: {self.symbol} {action} signal...')
                self.telegram.send_message(f"🟢 {action} {self.symbol} | TP: {request['tp']} | SL: {request['sl']}\n NB: use proper risk management")
                return True
            
        except Exception as e:
            print(f"❌ Error placing order: {e}")
            return False

    def run_Trades(self, market_bias, ltf_Bias, latest, current_price, THRESHOLD, symbol):
        result = None
        ltf_latest = latest.copy()  # ✅ Still a Series

        # print(f'Latest row:\n{ltf_latest}')

        if abs(current_price - ltf_latest['Fast_MA']) <= THRESHOLD:
            ltf_latest['Range'] = True  # ✅ This is now safe

            print(f'📌 Trading Decision - {symbol}: Market Bias={market_bias} | LTF Bias={ltf_Bias} | range: {abs(current_price - ltf_latest["Fast_MA"]) <= THRESHOLD}')

            if market_bias == "Bullish" and ltf_Bias == 'Buy' and current_price > ltf_latest['Fast_MA']:
                print(f"{symbol} - Confirmed Bullish Signal - Placing BUY order")
                result = self.place_order("buy", stop_loss=self.user_data['sl'], take_profit=self.user_data['tp'])
                self.TradesData = ltf_latest

            elif market_bias == "Bearish" and ltf_Bias == 'Sell' and current_price < ltf_latest['Fast_MA']:
                print(f"{symbol} - Confirmed Bearish Signal - Placing SELL order")
                result = self.place_order("sell", stop_loss=self.user_data['sl'], take_profit=self.user_data['tp'])
                self.TradesData = ltf_latest

            print(f"{symbol} - No action taken within range." if result is None else f"{symbol} - Action taken: {result}")

        else:
            print(f"{symbol} - No valid entry signal")

    def close(self):
        """Shutdown MT5 connection."""
        mt5.shutdown()
        print("Disconnected from MetaTrader 5.")
