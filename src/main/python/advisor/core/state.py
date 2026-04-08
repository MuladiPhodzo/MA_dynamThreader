import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from multiprocessing import Manager
from multiprocessing.managers import SyncManager
from typing import Any, Callable, Optional

from advisor.bootstrap.state_loader import StateStore
from advisor.utils.logging_setup import get_logger

logger = get_logger("_State_Manager_")

# =========================================================
# CONSTANTS
# =========================================================

class BotLifecycle(Enum):
    STARTING = 1
    RUNNING = 2
    RUNNING_BACKTEST = 3
    IDLE = 4
    DEGRADED = 5
    RECOVERING = 6
    STOPPING = 7
    STOPPED = 8

class symbolCycle(Enum):
    STAND_BY = 1
    INITIALIZING = 2
    BACKTESTING = 3
    READY = 4
    DEGRADED = 5

# =========================================================
# DATA STRUCTURES
# =========================================================
@dataclass
class Strategy:
    strategy_name: str
    strategy: Callable = None
    strategy_score: float = 0.0


@dataclass
class symbolStrategy:
    EMA: Any = None
    Volume: Any = None


@dataclass
class SymbolState:
    symbol: str = ''
    strategies: list[Strategy] = field(default_factory=list)
    score: float = 0.0
    last_backtest: Optional[datetime] = None
    enabled: bool = False
    state: symbolCycle = symbolCycle.STAND_BY
    meta: dict = field(
        default_factory=lambda: {
            "Total_trades": 0,
            "Total_signals": 0,
            "Pip_size": 0,
        }
    )
@dataclass
class BotState:
    version: str = "1.0"
    symbols: list[SymbolState] = field(default_factory=list)
    live_trading_enabled: bool = True

    backtest_running: bool = False
    last_backtest_run: Optional[datetime] = None
    next_backtest_run: Optional[datetime] = None

    state: BotLifecycle = BotLifecycle.STOPPED


@dataclass
class ClientState:
    server: str = None
    account_id: int = None
    connected: bool = False


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _build_symbols(symbol_map: dict[str, Any]) -> list[SymbolState]:
    return [
        SymbolState(
            symbol=k,
            strategies=[],
            score=v.get("score", 0.0) if isinstance(v, dict) else 0.0,
            last_backtest=StateManager._parse_dt(
                v.get("last_backtest") if isinstance(v, dict) else None
            ),
            enabled=_coerce_bool(v.get("enabled")) if isinstance(v, dict) else False,
            meta=v.get("meta", {}) if isinstance(v, dict) else {},
        )
        for k, v in (symbol_map or {}).items()
    ]


def _fresh_state() -> BotState:
    new = BotState()
    try:
        StateManager.save_bot_state(new)
    except Exception:
        pass
    logging.warning("State file missing or empty. Creating fresh state.")
    return new


# =========================================================
# STATE MANAGER
# =========================================================

class StateManager:
    def __init__(self, manager: SyncManager | None = None):
        # Runtime lifecycle state (NOT persisted here)
        if manager is None:
            manager = Manager()
        self._manager = manager
        self._lifecycle = manager.Value("i", BotLifecycle.STARTING.value)
        self.fetch_symbols = False
        self.bot = self.load_bot_state()
        self.bot.state = self.get_state()
    # -----------------------------------------------------
    # Lifecycle State (Runtime Only)
    # -----------------------------------------------------

    def set_state(self, state: BotLifecycle):
        self._lifecycle.value = state.value
        self.bot.state = state

    def get_state(self) -> BotLifecycle:
        return BotLifecycle(self._lifecycle.value)

    def set_symbol_state(self, name, new_state: symbolCycle):
        for sym in self.bot.symbols:
            if sym.symbol == name:
                sym.state = new_state
                break

    @property
    def last_backtest_run(self) -> Optional[datetime]:
        return self.bot.last_backtest_run

    @last_backtest_run.setter
    def last_backtest_run(self, value: Optional[datetime]) -> None:
        self.bot.last_backtest_run = value

    # -----------------------------------------------------
    # Datetime Helpers
    # -----------------------------------------------------

    @staticmethod
    def _serialize_dt(dt: Optional[datetime]):
        return dt.isoformat() if dt else None

    @staticmethod
    def _parse_dt(value: Optional[str]):
        if not value:
            return None
        return datetime.fromisoformat(value)

    # -----------------------------------------------------
    # LOAD BOT STATE
    # -----------------------------------------------------

    @staticmethod
    def load_bot_state() -> BotState:
        store = StateStore()
        if store.state.__eq__({}):
            return _fresh_state()

        try:

            symbols = _build_symbols(store.state.get("symbols", {}))
            if not symbols:
                logger.info("No symbols available in state file")

            return BotState(
                version=store.state.get("version", "1.0"),
                last_backtest_run=StateManager._parse_dt(store.state.get("last_backtest_run")),
                next_backtest_run=StateManager._parse_dt(store.state.get("next_backtest_run")),
                symbols=symbols,
                backtest_running=store.state.get("backtest_running", False),
                live_trading_enabled=store.state.get("live_trading_enabled", True),
            )

        except Exception as e:
            logging.critical(f"State file corrupted. Resetting. Error: {e}")
            return _fresh_state()

    # -----------------------------------------------------
    # SAVE BOT STATE (Atomic)
    # -----------------------------------------------------

    @staticmethod
    def save_bot_state(state: BotState):
        payload = {
            "version": state.version,
            "last_backtest_run": StateManager._serialize_dt(state.last_backtest_run),
            "next_backtest_run": StateManager._serialize_dt(state.next_backtest_run),
            "backtest_running": state.backtest_running,
            "live_trading_enabled": state.live_trading_enabled,
            "symbols": {
                sym.symbol: {
                    "score": sym.score,
                    "last_backtest": StateManager._serialize_dt(sym.last_backtest),
                    "enabled": sym.enabled,
                    "meta": sym.meta or {},
                }
                for sym in state.symbols
            }
        }

        try:
            StateStore.save_bot_state(payload)

        except Exception as e:
            logging.error(f"Failed to persist bot state: {e}")

    # -----------------------------------------------------
    # BACKTEST SCHEDULING
    # -----------------------------------------------------
    @staticmethod
    def is_backtest_due(state: BotState) -> bool:

        if state.backtest_running:
            return False

        if not state.next_backtest_run:
            return True

        return datetime.now(timezone.utc) >= state.next_backtest_run

    @staticmethod
    def schedule_next_backtest(state: BotState):

        now = datetime.now(timezone.utc)

        state.last_backtest_run = now
        state.next_backtest_run = now + timedelta(days=90)
