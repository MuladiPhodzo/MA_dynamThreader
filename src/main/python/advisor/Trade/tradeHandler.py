import MetaTrader5 as mt5
import datetime as dt
import logging
from advisor.Client import mt5Client
# from advisor.Trade.tradeStats import TradeStats as Stats

class mt5TradeHandler:
    def __init__(self, client: mt5Client, logger: logging, magic_number=8000):
        self.magic_number = magic_number
        self.client = client
        self.lot_size = float(client.lot_size)
        self.active_trades = {}

        self._refresh_symbol()
        self.logger = logger

    # -----------------------------------------------------------
    # Utility: Refresh symbol info (safe reconnection)
    # -----------------------------------------------------------
    def _refresh_symbol(self, symbol):
        info = mt5.symbol_info(symbol)

        if not info:
            mt5.initialize()
            info = mt5.symbol_info(symbol)

        if not info:
            raise RuntimeError(f"Symbol {symbol} unavailable")

        return info

    def _calculate_sl(self, direction, price, sl_points, point):
        if direction == "buy":
            return round(price - sl_points * point, 5)
        return round(price + sl_points * point, 5)

    def _calculate_tp(self, direction, price, tp_points, point):
        if direction == "buy":
            return round(price + tp_points * point, 5)
        return round(price - tp_points * point, 5)

    # -----------------------------------------------------------
    # Order Placement
    # -----------------------------------------------------------
    def place_market_order(self, symbol, direction, sl_points, tp_points):
        """
        direction: 'buy' | 'sell'
        """

        if direction not in ("buy", "sell"):
            raise ValueError("Invalid direction")

        info = self._refresh_symbol(symbol)
        if not info:
            raise RuntimeError("No info data")

        price = info.ask if direction == "buy" else info.bid
        order_type = mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL
        point = self.symbol_info.point

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": self.lot_size,
            "type": order_type,
            "price": price,
            "sl": self._calculate_sl(direction, price, sl_points, point),
            "tp": self._calculate_tp(direction, price, tp_points, point),
            "deviation": 10,
            "magic": self.magic_number,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = self.execute_trade(symbol, info, request, order_type, price)
        return {
            "ticket": result.order,
            "price": price,
            "sl": request["sl"],
            "tp": request["tp"],
            "volume": self.lot_size,
        }

    # -----------------------------------------------------------
    # Execute trade
    # -----------------------------------------------------------
    def execute_trade(self, symbol, symbol_info, request, action, price):
        try:
            if symbol_info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
                self.logger.error(f"❌ Trading disabled for {symbol}")
                return False

            result = mt5.order_send(request)

            if not result or result.retcode != mt5.TRADE_RETCODE_DONE:
                self.logger.error(f"❌ Order failed: {result}")
                return False

            self.logger.info(f"✅ {action.upper()} executed at {price}")

            # --------------------------------------
            # 🔥 Track ACTIVE TRADE
            # --------------------------------------
            ticket = result.order

            self.active_trades[ticket] = {
                "ticket": ticket,
                "action": action,
                "symbol": symbol,
                "open_price": price,
                "sl": request["sl"],
                "tp": request["tp"],
                "volume": request["volume"],
                "open_time": dt.datetime.now(),
            }

            self.logger.info(f"📌 Active Trades Updated → {len(self.active_trades)} open trades")

            # Record into persistent file
            mt5Client.dataHandler.save_trade(
                trade_data=request,
                file_format='json'
            )

            return True

        except Exception as e:
            self.logger.exception(f"❌ Error executing trade: {e}")
            return False
