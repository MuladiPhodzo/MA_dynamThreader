from __future__ import annotations


class PortfolioManager:
    """
    Multi-symbol portfolio manager for ranking and allocating fresh signals.
    """

    def __init__(
        self,
        capital: float = 100.0,
        risk_per_trade: float = 0.03,
        max_positions: int = 5,
        max_symbol_exposure: float = 0.2,
    ):
        self.capital = max(float(capital), 0.0)
        self.risk_per_trade = max(float(risk_per_trade), 0.0)
        self.max_positions = max(1, int(max_positions))
        self.max_symbol_exposure = max(float(max_symbol_exposure), 0.0)

        self.active_positions: dict[str, dict] = {}
        self.signal_pool: list[dict] = []

    def sync_active_positions(self, positions: list[dict] | dict[str, dict] | None) -> None:
        self.active_positions = {}
        if isinstance(positions, dict):
            for symbol, payload in positions.items():
                if symbol:
                    self.active_positions[str(symbol)] = dict(payload or {})
            return

        for payload in positions or []:
            if not isinstance(payload, dict):
                continue
            symbol = str(payload.get("symbol") or "").strip()
            if not symbol:
                continue
            self.active_positions[symbol] = dict(payload)

    def add_signal(self, symbol: str, signal: dict):
        normalized = self._normalize_signal(symbol, signal)
        if normalized is None:
            return
        self.signal_pool.append(normalized)

    def build_portfolio(self) -> list[dict]:
        if not self.signal_pool:
            return []

        available_slots = max(self.max_positions - len(self.active_positions), 0)
        if available_slots <= 0:
            self.signal_pool.clear()
            return []

        ranked = self._rank_signals(self.signal_pool)
        selected = ranked[:available_slots]
        trades = self._allocate(selected)

        self.signal_pool.clear()
        return trades

    def _rank_signals(self, signals: list[dict]) -> list[dict]:
        return sorted(
            signals,
            key=lambda item: (
                float(item.get("confidence", 0.0)),
                abs(float(item.get("metadata", {}).get("score", 0.0))),
            ),
            reverse=True,
        )

    def _allocate(self, signals: list[dict]) -> list[dict]:
        trades: list[dict] = []
        used_symbols = set(self.active_positions.keys())

        for sig in signals:
            symbol = sig["symbol"]
            if symbol in used_symbols:
                continue

            position_size = self._calculate_position_size(sig)
            if position_size <= 0:
                continue

            risk_amount = round(self.capital * self.risk_per_trade, 2)
            trade = {
                "symbol": symbol,
                "direction": sig["direction"],
                "confidence": float(sig.get("confidence", 0.0)),
                "position_size": position_size,
                "risk_amount": risk_amount,
                "metadata": dict(sig.get("metadata", {})),
            }
            trades.append(trade)
            used_symbols.add(symbol)

        return trades

    def _calculate_position_size(self, signal: dict) -> float:
        base_risk = self.capital * self.risk_per_trade
        metadata = signal.get("metadata", {}) if isinstance(signal.get("metadata"), dict) else {}

        pip_value = self._safe_float(metadata.get("pip_value"))
        sl_distance = self._safe_float(
            metadata.get("sl_distance", metadata.get("sl")),
        )
        if pip_value > 0 and sl_distance > 0:
            size = base_risk / max(sl_distance * pip_value, 1e-9)
        else:
            confidence = min(max(self._safe_float(signal.get("confidence"), 50.0) / 100.0, 0.0), 1.0)
            size = base_risk * confidence

        exposure_cap = self.capital * self.max_symbol_exposure if self.max_symbol_exposure > 0 else size
        size = min(size, exposure_cap)
        return round(max(size, 0.0), 2)

    def _normalize_signal(self, symbol: str, signal: dict) -> dict | None:
        if not symbol or not isinstance(signal, dict):
            return None

        direction = str(signal.get("direction") or signal.get("side") or "").strip().lower()
        if direction == "buy":
            normalized_direction = "Buy"
        elif direction == "sell":
            normalized_direction = "Sell"
        else:
            return None

        metadata = signal.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        confidence = self._safe_float(signal.get("confidence"), 0.0)
        return {
            "symbol": symbol,
            "direction": normalized_direction,
            "confidence": max(confidence, 0.0),
            "metadata": dict(metadata),
        }

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
