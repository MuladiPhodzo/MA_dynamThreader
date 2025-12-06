import MetaTrader5 as mt5
import pandas as pd
import datetime as dt
import logging
import sys
from advisor.Telegram.core import TelegramMessenger as Messenger
from advisor.Client import mt5Client
from advisor.Trade.tradeStats import TradeStats as Stats

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


class MT5TradingAlgorithm:
    def __init__(self, symbol, telegram: Messenger, user_data, magic_number=8000):

        self.symbol = symbol
        self.telegram = telegram
        self.magic_number = magic_number
        self.user_data = user_data
        self.lot_size = float(user_data.get("volume", 0.1))

        self.stats = Stats()

        # ensure symbol info exists
        self.refresh_symbol_info()

        # state
        self.current_position = None
        self.openTrades = 0
        self.opened = [False, False]

        # multi-timeframe data
        self.TradesData = {}   # MUST be a dict for .get() to work
        self.active_trades = {}     # key = ticket
        self.closed_trades = {}     # key = ticket

    # -----------------------------------------------------------
    # Utility: Refresh symbol info (safe reconnection)
    # -----------------------------------------------------------
    def refresh_symbol_info(self):
        self.symbol_info = mt5.symbol_info(self.symbol)
        if not self.symbol_info:
            logger.error(f"⚠️ Symbol info for {self.symbol} unavailable. Re-checking MT5 connection...")
            mt5.initialize()
            self.symbol_info = mt5.symbol_info(self.symbol)

        if not self.symbol_info:
            logger.error(f"❌ Fatal: Symbol {self.symbol} still not found.")
        else:
            logger.info(f"📌 Loaded symbol info: {self.symbol}")

    # -----------------------------------------------------------
    # Order Placement
    # -----------------------------------------------------------
    def place_order(self, action, stop_loss=100, take_profit=300):
        try:
            if action not in ["buy", "sell"]:
                raise ValueError(f"Invalid action '{action}'")

            if not self.symbol_info:
                self.refresh_symbol_info()
                if not self.symbol_info:
                    return False, None

            if not self.symbol_info.visible:
                mt5.symbol_select(self.symbol, True)

            tick = mt5.symbol_info_tick(self.symbol)
            if not tick:
                logger.error(f"❌ No tick data for {self.symbol}")
                return False, None

            price = tick.ask if action == "buy" else tick.bid
            order_type = mt5.ORDER_TYPE_BUY if action == "buy" else mt5.ORDER_TYPE_SELL
            point = self.symbol_info.point

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
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            logger.info(f"📤 placing {action.upper()} order: {request}")

            if True in self.opened:
                success = self.execute_trade(request, action, price)
                if success:
                    self.stats.log_trade(
                        symbol=self.symbol,
                        action=action,
                        price=price,
                        volume=self.lot_size,
                        sl=request["sl"],
                        tp=request["tp"],
                    )
                return success, request

            else:
                logger.warning(f"⚠️ Trading queue full for {self.symbol}")
                return False, request

        except Exception as e:
            logger.exception(f"❌ Error placing order: {e}")
            return False, None

    # -----------------------------------------------------------
    # Execute trade
    # -----------------------------------------------------------
    def execute_trade(self, request, action, price):
        try:

            if self.symbol_info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
                logger.error(f"❌ Trading disabled for {self.symbol}")
                return False

            result = mt5.order_send(request)

            if not result or result.retcode != mt5.TRADE_RETCODE_DONE:
                logger.error(f"❌ Order failed: {result}")
                return False

            logger.info(f"✅ {action.upper()} executed at {price}")

            # --------------------------------------
            # 🔥 Track ACTIVE TRADE
            # --------------------------------------
            ticket = result.order

            self.active_trades[ticket] = {
                "ticket": ticket,
                "action": action,
                "symbol": self.symbol,
                "open_price": price,
                "sl": request["sl"],
                "tp": request["tp"],
                "volume": request["volume"],
                "open_time": dt.datetime.now(),
            }

            logger.info(f"📌 Active Trades Updated → {len(self.active_trades)} open trades")

            # Record into persistent file
            mt5Client.dataHandler.save_trade(
                trade_data=request,
                file_format='json'
            )

            return True

        except Exception as e:
            logger.exception(f"❌ Error executing trade: {e}")
            return False

    def sync_closed_trades(self):
        """
        Pull closed trades from MT5 and update closed_trades dictionary.
        Moves trades from active → closed when detected.
        """

        try:
            utc_from = dt.datetime.now() - dt.timedelta(days=5)
            utc_to = dt.datetime.now()

            deals = mt5.history_deals_get(utc_from, utc_to)
            if not deals:
                return

            df = pd.DataFrame(list(deals), columns=deals[0]._asdict().keys())

            df = df[(df["symbol"] == self.symbol) & (df["magic"] == self.magic_number)]

            if df.empty:
                return

            for _, row in df.iterrows():
                ticket = row["order"]

                # Trade is still open → ignore
                if ticket in self.active_trades:
                    # trade closed → move to closed list
                    pl = row["profit"]
                    exit_price = row["price"]
                    close_time = row["time"]

                    self.closed_trades[ticket] = {
                        "ticket": ticket,
                        "symbol": self.symbol,
                        "open_price": self.active_trades[ticket]["open_price"],
                        "close_price": exit_price,
                        "volume": self.active_trades[ticket]["volume"],
                        "profit": pl,
                        "open_time": self.active_trades[ticket]["open_time"],
                        "close_time": close_time,
                        "duration_seconds": (close_time - self.active_trades[ticket]["open_time"]).seconds,
                        "reason": "SL/TP/Manual"
                    }

                    logger.info(f"📉 Trade closed: ticket={ticket}, profit={pl}")

                    del self.active_trades[ticket]

        except Exception as e:
            logger.exception(f"❌ Error syncing closed trades: {e}")

    # -----------------------------------------------------------
    # Detect proximity
    # -----------------------------------------------------------
    def detect_latest_price_proximity(self):
        PROXIMITY_TFS = ["15M", "30M", "1H", "2H", "4H"]
        results = {}

        # Ensure TradesData is dict
        if not isinstance(self.TradesData, dict):
            logger.error("TradesData must be a dict of {tf: df}")
            return False

        for tf_name in PROXIMITY_TFS:
            df = self.TradesData.get(tf_name)
            if df is None or df.empty:
                continue

            latest = df.iloc[-1]

            if "Fast_MA" not in latest or "Slow_MA" not in latest:
                continue

            price = latest["close"]
            fast = latest["Fast_MA"]
            fast_diff = abs(price - fast)

            threshold = (20 if "H" in tf_name else 10) * self.symbol_info.point

            results[tf_name] = {
                "fast_diff": fast_diff,
                "threshold": threshold,
                "is_close": fast_diff <= threshold,
                "price": price,
                "fast_ma": fast,
            }

        return self.all_timeframes_close(results)

    def all_timeframes_close(self, proximity_dict):
        if not proximity_dict:
            return False

        for tf, info in proximity_dict.items():
            if not info.get("is_close", False):
                return False

        return True

    # -----------------------------------------------------------
    # Main decision logic
    # -----------------------------------------------------------
    def run_trades(self, THRESHOLD, symbol):
        try:
            trend = self.TradesData.get("Main_Trend", None)

            if trend == "":
                logger.error("❌ Main_Trend missing.")
                return False

            # Safe check for M30 fast MA
            df_30 = self.TradesData.get("30M")
            if df_30 is None or df_30.empty:
                logger.error("⚠️ Missing 30M data")
                return False
            
            current_price = df_30["close"].iloc[-1]

            fast_ma_30 = df_30["Fast_MA"].iloc[-1]
            diff = abs(current_price - fast_ma_30)
            in_range = diff <= THRESHOLD

            logger.info(f"📌 {symbol} Market Bias={trend}, In Range={in_range}")

            if not self.detect_latest_price_proximity():
                logger.info(f"{symbol} Price not close across all TF → No trade")
                return False

            if trend == "Bearish":
                return self.place_order("buy",
                                        self.user_data["sl"],
                                        self.user_data["tp"])

            elif trend == "Bullish":
                return self.place_order("sell",
                                        self.user_data["sl"],
                                        self.user_data["tp"])

            return False

        except Exception as e:
            logger.exception(f"❌ Error in run_trades: {e}")
            return False

    # -----------------------------------------------------------
    # Stats & History
    # -----------------------------------------------------------
    def load_trades_history(self, days=1):
        try:
            utc_from = dt.datetime.now() - dt.timedelta(days=days)
            utc_to = dt.datetime.now()

            deals = mt5.history_deals_get(utc_from, utc_to)
            if not deals:
                return pd.DataFrame()

            df = pd.DataFrame(list(deals), columns=deals[0]._asdict().keys())
            df = df[(df["magic"] == self.magic_number) & (df["symbol"] == self.symbol)]
            if df.empty:
                return df

            self.stats.update_from_history(df)
            return df

        except Exception as e:
            logger.exception(f"❌ Error fetching trade history: {e}")
            return pd.DataFrame()
