import logging
from datetime import datetime, timedelta, timezone

from advisor.Trade.trateState import TradeStateManager
from advisor.core.health_bus import HealthBus
from advisor.core.state import BotLifecycle, StateManager

logger = logging.getLogger("RiskManager")


class RiskManager:
    def __init__(
        self,
        client,
        trade_state: TradeStateManager,
        state_manager: StateManager,
        health_bus: HealthBus,
        persistence=None,
        max_daily_loss_pct=0.05,
        max_total_dd_pct=0.15,
        max_trades_per_hour=10,
        max_symbol_exposure=2,
        max_consecutive_losses=5,
        max_risk_per_trade=0.01,
    ):
        self.client = client
        self.trade_state = trade_state
        self.state_manager = state_manager
        self.health_bus = health_bus
        self.persistence = persistence

        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_total_dd_pct = max_total_dd_pct
        self.max_trades_per_hour = max_trades_per_hour
        self.max_symbol_exposure = max_symbol_exposure
        self.max_consecutive_losses = max_consecutive_losses
        self.max_risk_per_trade = max_risk_per_trade

        self.daily_loss = 0.0
        self.peak_equity = float(self._equity())
        self.consecutive_losses = 0
        self.trade_timestamps: list[datetime] = []
        self.current_day = datetime.now(timezone.utc).date()

    def validate(self, signal):
        self._reset_daily_if_needed()
        if not self._check_daily_loss():
            return False, 0.0
        if not self._check_total_drawdown():
            return False, 0.0
        if not self._check_trade_frequency():
            return False, 0.0
        if not self._check_symbol_exposure(signal.symbol):
            return False, 0.0
        if not self._check_consecutive_losses():
            return False, 0.0
        lot = self._calculate_position_size(signal)
        return lot > 0, lot

    def _equity(self) -> float:
        getter = getattr(self.client, "get_equity", None)
        if callable(getter):
            return float(getter())
        info = getattr(self.client, "account_info", None)
        if isinstance(info, dict):
            return float(info.get("equity", info.get("balance", 0.0)))
        return 0.0

    def _check_daily_loss(self):
        return abs(self.daily_loss) < self._equity() * self.max_daily_loss_pct

    def _check_total_drawdown(self):
        equity = max(self._equity(), 0.0001)
        dd = (self.peak_equity - equity) / max(self.peak_equity, 0.0001)
        return dd < self.max_total_dd_pct

    def _check_trade_frequency(self):
        last_hour = datetime.now(timezone.utc) - timedelta(hours=1)
        recent = [t for t in self.trade_timestamps if t >= last_hour]
        return len(recent) < self.max_trades_per_hour

    def _check_symbol_exposure(self, symbol):
        return self.trade_state.count_symbol(symbol) < self.max_symbol_exposure

    def _check_consecutive_losses(self):
        return self.consecutive_losses < self.max_consecutive_losses

    def _calculate_position_size(self, signal):
        sl = float(getattr(signal, "sl", 0) or 0)
        if sl <= 0:
            return 0.0
        equity = self._equity()
        risk_amount = equity * self.max_risk_per_trade
        return max(round(risk_amount / sl, 2), 0.01)

    def register_trade_open(self):
        self.trade_timestamps.append(datetime.now(timezone.utc))

    def register_trade_close(self, profit: float):
        self.daily_loss += min(0.0, profit)
        self.consecutive_losses = self.consecutive_losses + 1 if profit < 0 else 0
        self.peak_equity = max(self.peak_equity, self._equity())

    def _reset_daily_if_needed(self):
        today = datetime.now(timezone.utc).date()
        if today != self.current_day:
            self.current_day = today
            self.daily_loss = 0.0

    def halt(self, reason: str):
        logger.critical("TRADING HALTED: %s", reason)
        self.health_bus.update("RiskEngine", "HALTED", {"reason": reason})
        self.state_manager.set_state(BotLifecycle.DEGRADED)
