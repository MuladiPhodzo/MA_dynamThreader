import datetime as dt
import logging

import MetaTrader5 as mt5


class mt5TradeHandler:
    def __init__(self, client, logger: logging.Logger, magic_number=8000):
        self.magic_number = magic_number
        self.client = client
        self.logger = logger
        self.active_trades = {}

    def _refresh_symbol(self, symbol):
        info = mt5.symbol_info(symbol)
        if info:
            return info
        mt5.initialize()
        return mt5.symbol_info(symbol)

    @staticmethod
    def _calculate_sl(direction, price, sl_points, point):
        if direction == "buy":
            return round(price - sl_points * point, 5)
        return round(price + sl_points * point, 5)

    @staticmethod
    def _calculate_tp(direction, price, tp_points, point):
        if direction == "buy":
            return round(price + tp_points * point, 5)
        return round(price - tp_points * point, 5)

    def place_market_order(self, symbol, side, lot, sl_points, tp_points):
        if side not in ("buy", "sell"):
            raise ValueError("Invalid direction")

        info = self._refresh_symbol(symbol)
        if not info:
            raise RuntimeError(f"Symbol {symbol} unavailable")

        price = info.ask if side == "buy" else info.bid
        point = info.point
        order_type = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(lot),
            "type": order_type,
            "price": price,
            "sl": self._calculate_sl(side, price, sl_points, point),
            "tp": self._calculate_tp(side, price, tp_points, point),
            "deviation": 10,
            "magic": self.magic_number,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if not result or result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"Order failed: {result}")

        trade = {
            "ticket": result.order,
            "symbol": symbol,
            "side": side,
            "price": price,
            "sl": request["sl"],
            "tp": request["tp"],
            "volume": float(lot),
            "open_time": dt.datetime.now(dt.timezone.utc),
        }
        self.active_trades[result.order] = trade
        return trade
