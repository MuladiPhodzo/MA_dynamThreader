import MetaTrader5 as mt5
import pandas as pd
import datetime as dt
from advisor.Telegram import Messanger
from advisor.Client import mt5Client


class MT5TradingAlgorithm:
    def __init__(self, symbol, telegram: Messanger.TelegramMessenger, user_data, magic_number=8000):
        """
        Initialize the MT5 trading algorithm.
        :param symbol: The trading symbol (e.g., 'USDJPY').
        :param telegram: Telegram messenger object for alerts.
        :param user_data: Dict with trading parameters (volume, sl, tp).
        :param magic_number: Unique identifier for this strategy's trades.
        """
        self.symbol = symbol
        self.lot_size = float(user_data.get("volume", 0.1))
        self.magic_number = magic_number
        self.user_data = user_data
        self.current_position = None  # 'buy', 'sell', or None
        self.telegram = telegram
        self.opened = [False, False, False]
        self.openTrades = 0

        # Initialize TradesData properly
        self.TradesData = pd.DataFrame()

    def place_order(self, action, stop_loss=100, take_profit=300):
        """
        Place a buy or sell order on MT5.
        :param action: 'buy' or 'sell'.
        :param stop_loss: SL in points.
        :param take_profit: TP in points.
        :return: (success: bool, request: dict | None)
        """
        try:
            if action not in ["buy", "sell"]:
                raise ValueError(
                    f"Invalid action '{action}', must be 'buy' or 'sell'.")

            symbol_info = mt5.symbol_info(self.symbol)
            if symbol_info is None:
                print(f"❌ Symbol {self.symbol} not found.")
                return False, None

            if not symbol_info.visible:
                if not mt5.symbol_select(self.symbol, True):
                    print(f"❌ Failed to select symbol {self.symbol}.")
                    return False, None

            # Get current price safely
            tick = mt5.symbol_info_tick(self.symbol)
            if tick is None:
                print(f"❌ Could not fetch tick data for {self.symbol}.")
                return False, None

            price = tick.ask if action == "buy" else tick.bid
            if price <= 0:
                print(f"❌ Invalid price received for {self.symbol}.")
                return False, None

            order_type = mt5.ORDER_TYPE_BUY if action == "buy" else mt5.ORDER_TYPE_SELL
            point = symbol_info.point

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": self.symbol,
                "volume": self.lot_size,
                "type": order_type,
                "price": price,
                "sl": round(price - stop_loss * point if action == "buy" else price + stop_loss * point, 5),
                "tp": round(price + take_profit * point if action == "buy" else price - take_profit * point, 5),
                "deviation": 10,
                "magic": self.magic_number,
                "comment": f"{action.capitalize()} trade by Moving Average strategy",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            return self.executeTrade(request, symbol_info, action, price)

        except Exception as e:
            print(f"❌ Error placing order: {e}")
            return False

    def run_Trades(self, market_bias, ltf_Bias: pd.DataFrame, latest, current_price, THRESHOLD, symbol):
        """ Execute trading logic based on market and LTF bias.
        :param market_bias: 'Bullish' or 'Bearish' for the higher timeframe.
        :param ltf_Bias: 'Buy' or 'Sell' for the lower timeframe.
        :param latest: Latest data containing indicators.
        :param current_price: Current market price.
        :param THRESHOLD: Acceptable range threshold."""

        try:
            ltf_latest = latest.copy()
            diff = abs(current_price - ltf_latest["Fast_MA"])
            in_range = diff <= THRESHOLD
            ltf_latest["Range"] = in_range

            print(
                f"📌 Decision {symbol}: Market Bias={market_bias}, LTF Bias={ltf_Bias}, In Range={in_range}")

            if not in_range:
                print(f"{symbol} - No valid entry signal.")
                return False

            if market_bias == "Bullish" and ltf_Bias == "Buy" and current_price > ltf_latest["Fast_MA"]:
                print(f"{symbol} - Confirmed Bullish Signal → BUY")
                return self.place_order("buy", self.user_data["sl"], self.user_data["tp"])

            elif market_bias == "Bearish" and ltf_Bias == "Sell" and current_price < ltf_latest["Fast_MA"]:
                print(f"{symbol} - Confirmed Bearish Signal → SELL")
                return self.place_order("sell", self.user_data["sl"], self.user_data["tp"])

            else:
                print(f"{symbol} - Conditions not met for trade.")
                return False

        except KeyError as e:
            print(f"❌ Missing expected key in latest data: {e}")
            return False
        except Exception as e:
            print(f"❌ Error in run_Trades: {e}")
            return False

    def Load_TradesHistory(self, days=1):
        """
        Check for closed trades in the last N days for this strategy (magic number).
        Returns a DataFrame with profit/loss.
        """
        try:
            utc_from = dt.datetime.now() - dt.timedelta(days=days)
            utc_to = dt.datetime.now()

            # Fetch deals history from MT5
            deals = mt5.history_deals_get(utc_from, utc_to)
            if deals is None:
                print("❌ No deal history retrieved.")
                return pd.DataFrame()

            # Convert to DataFrame
            deals_df = pd.DataFrame(
                list(deals), columns=deals[0]._asdict().keys())
            if deals_df.empty:
                print("⚠️ No closed trades in this period.")
                return deals_df

            # Filter only this strategy's trades (by magic number and symbol)
            deals_df = pd.DataFrame(deals_df[(deals_df["magic"] == self.magic_number) & (
                deals_df["symbol"] == self.symbol)])

            if deals_df.empty:
                print("⚠️ No closed trades for this strategy.")
                return deals_df

            # Mark trades as Profit or Loss
            deals_df["P/L"] = deals_df["profit"].apply(
                lambda p: "Profit" if p > 0 else "Loss" if p < 0 else "BreakEven")

            # Print summary
            print("📊 Closed Trades Summary:")
            for _, row in deals_df.iterrows():
                print(
                    f"  {row['symbol']} | Ticket: {row['ticket']} | Profit: {row['profit']} | {row['Result']}")

            return deals_df

        except Exception as e:
            print(f"❌ Error fetching closed trades: {e}")
            return pd.DataFrame()

    def executeTrade(self, request, symbol_info, action, price):
        try:
            # Send order
            while (False not in self.opened):
                if symbol_info.trade_mode == 0:
                    result = mt5.order_send(request)
                    if result is None:
                        print(
                            "❌ mt5.order_send() returned None — check if MetaTrader is initialized and logged in.")

                    if result.retcode != mt5.TRADE_RETCODE_DONE:
                        print(f"❌ Order failed: {result.retcode}")

                    else:
                        print(
                            f"✅ {action.capitalize()} order placed at {price}. Retcode: {result.retcode}")
                        self.telegram.send_message(
                            f"🟢Placed {action} {self.symbol} @ {request['price']} | TP: {request['tp']} | SL: {request['sl']}\n NB: use proper risk management")

                        self.TradesData.add(request)
                        self.TradesData = self.TradesData.drop(
                            columns=['type_time', 'comment', 'type_filling', 'deviation'])
                        self.current_position = action
                        self.openTrades += 1
                        self.opened[self.openTrades] = True
                        print(
                            f"🟢 {self.symbol} Placing{str(action).upper()} order...")
                        return True, request

        except Exception as e:
            raise Exception(f"❌ Error executing trade: {e}")

        finally:
            print(
                f'sending Telegram: {self.symbol} {action} signal via telegram...')
            self.telegram.send_message(
                f"🟢 {action} {self.symbol} | TP: {request['tp']} | SL: {request['sl']}\n NB: use proper risk management")
            mt5Client.dataHandler._toCSV(
                "Trade/trades_log.csv", request, dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
            self.TradesData.add(request)

            return True
