from datetime import datetime, timedelta
from dataclasses import dataclass

@dataclass
class Signal:
    signal_id: str
    symbol: str
    signal_data: dict
    timestamp: datetime

class SignalStore:
    def __init__(self):
        self.signals: dict[str, list[Signal]] = {}

    def add_signal(self, signal: dict):
        try:
            sig = Signal(signal.get("id"), signal.get("symbol"), signal.get("data"), timestamp=datetime.now(datetime.timezone.utc))
            sig_ls = self.signals.get(signal.get("symbol"))
            sig_ls.append(sig)
        except Exception:
            pass

    def get_latest(self, symbol):
        sym_signals = self.signals[symbol]
        for signal in sym_signals:
            if signal.timestamp > timedelta(minutes=2):
                continue

            return signal
