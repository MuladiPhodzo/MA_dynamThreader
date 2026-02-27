from datetime import datetime, timezone, timedelta
from Client.mt5Client import MetaTrader5Client
from Trade.trateState import TradeStateManager
from advisor.core.health_bus import HealthBus
from advisor.core.state import StateManager
import logging

logger = logging.getLogger("RiskManager")


class RiskManager:

    def __init__(
        self,
        client: MetaTrader5Client   ,
        trade_state: TradeStateManager,
        state_manager: StateManager,
        health_bus: HealthBus,
        persistence,
        max_daily_loss_pct=0.05,
        max_total_dd_pct=0.15,
        max_trades_per_hour=10,
        max_symbol_exposure=2,
        max_consecutive_losses=5,
        equity_slope_window=20
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
        self.equity_slope_window = equity_slope_window

        self._load_state()
        self.current_day = datetime.now(timezone.utc).date()

    # -------------------------------------------------
    # PUBLIC ENTRY POINT
    # -------------------------------------------------
    def validate_trade(self, signal):
        if not self._check_daily_loss():
            return False

        if not self._check_total_drawdown():
            return False

        if not self._check_trade_frequency():
            return False

        if not self._check_symbol_exposure(signal.symbol):
            return False

        if not self._check_consecutive_losses():
            return False

        if not self._check_equity_curve():
            return False

        return True

    # -------------------------------------------------
    # RISK CHECKS
    # -------------------------------------------------
    def _check_daily_loss(self):
        equity = self.client.get_equity()
        allowed = equity * self.max_daily_loss_pct

        if abs(self.daily_loss) >= allowed:
            self._halt("Daily loss limit breached")
            return False
        return True

    def _check_max_trades(self):
        open_trades = len(self.trade_state.get_active_trades())

        if open_trades >= self.max_concurrent_trades:
            logger.info("Max concurrent trades reached.")
            return False

        return True

    def _check_total_drawdown(self):
        equity = self.client.get_equity()

        dd = (self.peak_equity - equity) / self.peak_equity

        if dd >= self.max_total_dd_pct:
            self._halt("Total drawdown breached")
            return False

        return True

    def _check_trade_frequency(self):
        now = datetime.now(timezone.utc)
        last_hour = now - timedelta(hours=1)

        recent_trades = [
            t for t in self.trade_timestamps
            if t >= last_hour
        ]

        if len(recent_trades) >= self.max_trades_per_hour:
            logger.warning("Trade frequency limit reached")
            return False

        return True

    def _check_symbol_exposure(self, symbol):
        count = self.trade_state.count_symbol(symbol)
        if count >= self.max_symbol_exposure:
            return False
        return True

    def _check_consecutive_losses(self):
        if self.consecutive_losses >= self.max_consecutive_losses:
            self._halt("Max consecutive losses reached")
            return False
        return True

    def _check_equity_curve(self):
        if len(self.equity_history) < self.equity_slope_window:
            return True

        y = self.equity_history[-self.equity_slope_window:]
        x = list(range(len(y)))

        slope = self._linear_regression_slope(x, y)

        if slope < 0:
            logger.warning("Equity slope negative — protection triggered")
            return False

        return True

    # -------------------------------------------------
    # POSITION SIZING
    # -------------------------------------------------

    def _calculate_position_size(self, signal):

        equity = self.client.get_equity()

        risk_amount = equity * self.max_risk_per_trade

        if not signal.sl or signal.sl <= 0:
            logger.warning("Invalid SL for risk calculation.")
            return 0

        pip_value = self.client.get_pip_value(signal.symbol)

        lot = risk_amount / (signal.sl * pip_value)

        lot = self._normalize_lot(signal.symbol, lot)

        return lot

    def _normalize_lot(self, symbol, lot):
        info = self.client.get_symbol_info(symbol)
        min_lot = info.min_lot
        lot_step = info.lot_step

        lot = max(min_lot, lot)
        lot = round(lot / lot_step) * lot_step

        return lot

    # -------------------------------------------------
    # LOSS TRACKING
    # -------------------------------------------------
    def register_trade_open(self):
        now = datetime.now(timezone.utc)
        self.trade_timestamps.append(now)
        self.persistence.save("trade_timestamps", self.trade_timestamps)

    def register_trade_close(self, profit):
        self.daily_loss += min(0, profit)

        if profit < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        equity = self.client.get_equity()
        self.peak_equity = max(self.peak_equity, equity)
        self.equity_history.append(equity)

        self._persist_state()

    def _reset_daily_if_needed(self):
        today = datetime.now(timezone.utc).date()
        if today != self.current_day:
            self.current_day = today
            self.daily_loss = 0

    # =====================================================
    # HALT
    # =====================================================
    def _halt(self, reason):
        logger.critical(f"TRADING HALTED: {reason}")

        self.health_bus.update("RiskEngine", "HALTED", {"reason": reason})

        self.state_manager.set_state(self.state_manager.state.DEGRADED)

    # =====================================================
    # UTIL
    # =====================================================

    def _linear_regression_slope(self, x, y):
        n = len(x)
        mean_x = sum(x) / n
        mean_y = sum(y) / n

        num = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
        den = sum((x[i] - mean_x) ** 2 for i in range(n))

        return num / den if den != 0 else 0

    # =====================================================
    # PERSISTENCE
    # =====================================================
    def _persist_state(self):
        self.persistence.save("daily_loss", self.daily_loss)
        self.persistence.save("peak_equity", self.peak_equity)
        self.persistence.save("consecutive_losses", self.consecutive_losses)
        self.persistence.save("equity_history", self.equity_history)

    def _load_state(self):
        self.daily_loss = self.persistence.load("daily_loss", 0)
        self.peak_equity = self.persistence.load("peak_equity", self.client.get_equity())
        self.consecutive_losses = self.persistence.load("consecutive_losses", 0)
        self.equity_history = self.persistence.load("equity_history", [])
        self.trade_timestamps = self.persistence.load("trade_timestamps", [])
