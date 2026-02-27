import datetime as dt

import MetaTrader5 as mt5


class TradeStateManager:
    def __init__(self, symbol: str | None = None, magic_number: int = 8000):
        self.symbol = symbol
        self.magic_number = magic_number
        self.active: dict[int, dict] = {}
        self.closed: dict[int, dict] = {}

    def register_open(self, trade_info: dict) -> None:
        ticket = trade_info.get("ticket")
        if ticket is None:
            return
        self.active[ticket] = {**trade_info, "open_time": dt.datetime.now(dt.timezone.utc)}

    def get_active_trades(self):
        return list(self.active.values())

    def count_symbol(self, symbol: str) -> int:
        return sum(1 for trade in self.active.values() if trade.get("symbol") == symbol)

    def sync_closed(self):
        utc_from = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=5)
        deals = mt5.history_deals_get(utc_from, dt.datetime.now(dt.timezone.utc))
        if not deals:
            return

        for deal in deals:
            if self.symbol and deal.symbol != self.symbol:
                continue
            if deal.magic != self.magic_number:
                continue

            ticket = deal.order
            if ticket not in self.active:
                continue

            open_trade = self.active.pop(ticket)
            self.closed[ticket] = {
                "ticket": ticket,
                "symbol": deal.symbol,
                "open_price": open_trade.get("price"),
                "close_price": deal.price,
                "profit": deal.profit,
                "open_time": open_trade.get("open_time"),
                "close_time": dt.datetime.fromtimestamp(deal.time, tz=dt.timezone.utc),
            }
