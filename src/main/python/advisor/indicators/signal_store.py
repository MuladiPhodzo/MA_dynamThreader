from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass
class Signal:
    signal_id: str
    symbol: str
    side: str
    sl: float
    tp: float
    data: dict[str, Any]
    timestamp: datetime

    @property
    def id(self) -> str:
        return self.signal_id

    def is_valid(self) -> bool:
        if self.side not in {"buy", "sell"}:
            return False
        if self.sl is None or self.tp is None:
            return False
        return True


class SignalStore:
    def __init__(self):
        self.signals: dict[str, list[Signal]] = {}

    def add_signal(self, payload: dict[str, Any]) -> None:
        symbol = payload.get("symbol")
        if not symbol:
            return

        signal = Signal(
            signal_id=payload.get("id") or f"{symbol}:{datetime.now(timezone.utc).isoformat()}",
            symbol=symbol,
            side=payload.get("side", "").lower(),
            sl=float(payload.get("sl", 0)),
            tp=float(payload.get("tp", 0)),
            data=payload.get("data", {}),
            timestamp=payload.get("timestamp", datetime.now(timezone.utc)),
        )
        self.signals.setdefault(symbol, []).append(signal)

    def get_latest(self, symbol: str, max_age_minutes: int = 2) -> Signal | None:
        for signal in reversed(self.signals.get(symbol, [])):
            if datetime.now(timezone.utc) - signal.timestamp <= timedelta(minutes=max_age_minutes):
                return signal
        return None
