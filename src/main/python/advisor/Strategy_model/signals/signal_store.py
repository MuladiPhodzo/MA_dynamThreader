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
    confidence: float
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
    def __init__(self, max_per_symbol: int = 1000):
        self.signals: dict[str, list[Signal]] = {}
        self.max_per_symbol = max(10, int(max_per_symbol))

    def add_signal(self, payload: dict[str, Any]) -> None:
        symbol = payload.get("symbol")
        if not symbol:
            return
        raw_data = payload.get("data", {})
        data = raw_data if isinstance(raw_data, dict) else {}

        timestamp = payload.get("timestamp", datetime.now(timezone.utc))
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp)
            except Exception:
                timestamp = datetime.now(timezone.utc)

        signal = Signal(
            signal_id=payload.get("id") or f"{symbol}:{datetime.now(timezone.utc).isoformat()}",
            symbol=symbol,
            side=payload.get("side", "").lower(),
            sl=float(payload.get("sl", 0)),
            tp=float(payload.get("tp", 0)),
            confidence=float(payload.get("confidence", data.get("confidence", 50)) or 50),
            data=data,
            timestamp=timestamp,
        )
        bucket = self.signals.setdefault(symbol, [])
        bucket.append(signal)
        if len(bucket) > self.max_per_symbol:
            self.signals[symbol] = bucket[-self.max_per_symbol:]

    def get_latest(self, symbol: str, max_age_minutes: int = 2) -> Signal | None:
        for signal in reversed(self.signals.get(symbol, [])):
            if datetime.now(timezone.utc) - signal.timestamp <= timedelta(minutes=max_age_minutes):
                return signal
        return None

    def snapshot_latest(self, max_age_minutes: int = 15) -> dict[str, dict[str, Any]]:
        now = datetime.now(timezone.utc)
        payload: dict[str, dict[str, Any]] = {}
        for symbol, signals in self.signals.items():
            if not signals:
                continue
            latest = signals[-1]
            if now - latest.timestamp > timedelta(minutes=max_age_minutes):
                continue
            payload[symbol] = {
                "id": latest.signal_id,
                "symbol": latest.symbol,
                "side": latest.side,
                "sl": latest.sl,
                "tp": latest.tp,
                "confidence": latest.confidence,
                "data": latest.data,
                "timestamp": latest.timestamp.isoformat(),
            }
        return payload

    def load_latest(self, payload: dict[str, dict[str, Any]]) -> None:
        for symbol, raw in payload.items():
            if not isinstance(raw, dict):
                continue
            ts = raw.get("timestamp")
            try:
                timestamp = datetime.fromisoformat(ts) if ts else datetime.now(timezone.utc)
            except Exception:
                timestamp = datetime.now(timezone.utc)
            raw_data = raw.get("data", {})
            data = raw_data if isinstance(raw_data, dict) else {}
            signal = Signal(
                signal_id=str(raw.get("id") or f"{symbol}:{timestamp.isoformat()}"),
                symbol=str(raw.get("symbol") or symbol),
                side=str(raw.get("side") or "").lower(),
                sl=float(raw.get("sl", 0)),
                tp=float(raw.get("tp", 0)),
                confidence=float(raw.get("confidence", data.get("confidence", 50)) or 50),
                data=data,
                timestamp=timestamp,
            )
            self.signals[symbol] = [signal]
