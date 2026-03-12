from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from advisor.core.state import BotState, SymbolState


@dataclass
class SymbolTelemetry:
    symbol: str
    enabled: bool = False
    last_state_sync: datetime | None = None
    last_data_fetch: datetime | None = None
    last_signal_time: datetime | None = None
    last_trade_time: datetime | None = None
    last_error_time: datetime | None = None
    last_error: str | None = None
    data_fetch_count: int = 0
    signal_count: int = 0
    trade_count: int = 0
    error_count: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

class SymbolWatch:
    def __init__(self, botState: BotState):
        self.bot = botState
        self.all_symbols: list[SymbolState] = list(self.bot.symbols or [])
        self.active_symbols: list[SymbolState] = []
        self.telemetry: dict[str, SymbolTelemetry] = {}
        self.activate_symbols()
        self._sync_telemetry()

    def activate_symbols(self):
        self.active_symbols = [sym for sym in self.all_symbols if getattr(sym, "enabled", False)]

    def all_symbol_names(self) -> list[str]:
        return [getattr(sym, "symbol", sym) for sym in self.all_symbols]

    def active_symbol_names(self) -> list[str]:
        return [getattr(sym, "symbol", sym) for sym in self.active_symbols]

    def refresh(self) -> None:
        self.all_symbols = list(self.bot.symbols or [])
        self.activate_symbols()
        self._sync_telemetry()

    def set_enabled(self, symbol: str, enabled: bool) -> None:
        for sym in self.all_symbols:
            if sym.symbol == symbol:
                sym.enabled = enabled
                break
        self.activate_symbols()
        self._sync_telemetry()

    def is_active(self, symbol: str) -> bool:
        return symbol in self.active_symbol_names()

    def mark_data_fetch(self, symbol: str) -> None:
        telem = self._get_telemetry(symbol)
        telem.last_data_fetch = self._now()
        telem.data_fetch_count += 1

    def mark_signal(self, symbol: str) -> None:
        telem = self._get_telemetry(symbol)
        telem.last_signal_time = self._now()
        telem.signal_count += 1

    def mark_trade(self, symbol: str) -> None:
        telem = self._get_telemetry(symbol)
        telem.last_trade_time = self._now()
        telem.trade_count += 1

    def mark_error(self, symbol: str, message: str) -> None:
        telem = self._get_telemetry(symbol)
        telem.last_error_time = self._now()
        telem.last_error = message
        telem.error_count += 1

    def set_meta(self, symbol: str, key: str, value: Any) -> None:
        telem = self._get_telemetry(symbol)
        telem.meta[key] = value

    def get_telemetry(self, symbol: str) -> SymbolTelemetry | None:
        return self.telemetry.get(symbol)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            sym: {
                "enabled": telem.enabled,
                "last_state_sync": telem.last_state_sync,
                "last_data_fetch": telem.last_data_fetch,
                "last_signal_time": telem.last_signal_time,
                "last_trade_time": telem.last_trade_time,
                "last_error_time": telem.last_error_time,
                "last_error": telem.last_error,
                "data_fetch_count": telem.data_fetch_count,
                "signal_count": telem.signal_count,
                "trade_count": telem.trade_count,
                "error_count": telem.error_count,
                "meta": dict(telem.meta),
            }
            for sym, telem in self.telemetry.items()
        }

    def _sync_telemetry(self) -> None:
        now = self._now()
        active = {sym.symbol for sym in self.active_symbols}
        for sym in self.all_symbols:
            telem = self.telemetry.get(sym.symbol)
            if telem is None:
                telem = SymbolTelemetry(symbol=sym.symbol)
                self.telemetry[sym.symbol] = telem
            telem.enabled = sym.symbol in active
            telem.last_state_sync = now
            if sym.meta:
                telem.meta.update(sym.meta)

    def _get_telemetry(self, symbol: str) -> SymbolTelemetry:
        telem = self.telemetry.get(symbol)
        if telem is None:
            telem = SymbolTelemetry(symbol=symbol)
            self.telemetry[symbol] = telem
        return telem

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
