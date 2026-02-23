import datetime as dt
import MetaTrader5 as mt5

class TradeStateManager:

    def __init__(self, symbol, magic_number):
        self.symbol = symbol
        self.magic_number = magic_number
        self.active = {}
        self.closed = {}

    def register_open(self, trade_info):
        ticket = trade_info["ticket"]
        self.active[ticket] = {
            **trade_info,
            "open_time": dt.datetime.now(dt.datetime.timezone.utc)
        }

    def sync_closed(self):

        utc_from = dt.datetime.now(dt.datetime.timezone.utc) - dt.timedelta(days=5)
        deals = mt5.history_deals_get(utc_from, dt.datetime.now(dt.datetime.timezone.utc))

        if not deals:
            return

        for deal in deals:

            if deal.symbol != self.symbol:
                continue

            if deal.magic != self.magic_number:
                continue

            ticket = deal.order

            if ticket not in self.active:
                continue

            open_trade = self.active.pop(ticket)

            self.closed[ticket] = {
                "ticket": ticket,
                "symbol": self.symbol,
                "open_price": open_trade["price"],
                "close_price": deal.price,
                "profit": deal.profit,
                "open_time": open_trade["open_time"],
                "close_time": dt.datetime.fromtimestamp(deal.time),
            }
