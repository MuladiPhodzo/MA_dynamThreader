import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from multiprocessing import Manager
from multiprocessing.managers import SyncManager
from pathlib import Path
from typing import Callable, Optional

from advisor.core.locks import STATE_LOCK


# =========================================================
# CONSTANTS
# =========================================================

STATE_FILE = Path("bot_state.json")

class BotLifecycle(Enum):
    STARTING = 1
    RUNNING = 2
    RUNNING_BACKTEST = 3
    IDLE = 4
    DEGRADED = 5
    RECOVERING = 6
    STOPPING = 7
    STOPPED = 8


# =========================================================
# DATA STRUCTURES
# =========================================================
@dataclass
class Strategy:
    strategy_name: str
    strategy: Callable = None
    strategy_score: float = 0.0


@dataclass
class SymbolState:
    symbol: str = ''
    strategies: list[Strategy] = None
    score: float = 0.0
    last_backtest: Optional[datetime] = None
    enabled: bool = False
    meta: dict = {}


@dataclass
class BotState:
    version: str = "1.0"
    symbols: list[SymbolState] = None
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
# STATE MANAGER
# =========================================================

class StateManager:
    def __init__(self, manager: SyncManager | None = None):
        # Runtime lifecycle state (NOT persisted here)
        if manager is None:
            manager = Manager()
        self._manager = manager
        self._lifecycle = manager.Value("i", BotLifecycle.STARTING.value)
        self.bot = self.load_bot_state()
        self.bot.state = self.get_state()
    # -----------------------------------------------------
    # Lifecycle State (Runtime Only)
    # -----------------------------------------------------

    def set_state(self, state: BotLifecycle):
        self._lifecycle.value = state.value

    def get_state(self) -> BotLifecycle:
        return BotLifecycle(self._lifecycle.value)

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

        with STATE_LOCK:
            if not STATE_FILE.exists():
                STATE_FILE.mkdir()
                new = BotState()
                StateManager.save_bot_state(new)
                logging.warning("State file not found. Creating fresh state.")
                return new

            try:
                with open(STATE_FILE, "r") as f:
                    raw = json.load(f)

                symbols = [
                    SymbolState(
                        symbol=k,
                        strategies=[],
                        score=v.get("score", 0.0),
                        last_backtest=StateManager._parse_dt(v.get("last_backtest")),
                        enabled=v.get("enabled", False)
                    )
                    for k, v in raw.get("symbols", {}).items()
                ]

                return BotState(
                    version=raw.get("version", "1.0"),
                    last_backtest_run=StateManager._parse_dt(raw.get("last_backtest_run")),
                    next_backtest_run=StateManager._parse_dt(raw.get("next_backtest_run")),
                    symbols=symbols,
                    backtest_running=raw.get("backtest_running", False),
                    live_trading_enabled=raw.get("live_trading_enabled", True),
                )

            except Exception as e:
                logging.critical(f"State file corrupted. Resetting. Error: {e}")
                return BotState()

    # -----------------------------------------------------
    # SAVE BOT STATE (Atomic)
    # -----------------------------------------------------

    @staticmethod
    def save_bot_state(state: BotState):

        with STATE_LOCK:

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
                        "enabled": sym.enabled
                    }
                    for sym in state.symbols
                }
            }

            tmp = STATE_FILE.with_suffix(".tmp")

            try:
                with open(tmp, "w") as f:
                    json.dump(payload, f, indent=2)

                tmp.replace(STATE_FILE)

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
