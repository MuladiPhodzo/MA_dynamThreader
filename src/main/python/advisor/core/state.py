# core/state.py
from multiprocessing import Manager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict
# core/state.py

import json
from enum import Enum
from pathlib import Path
from core.locks import STATE_LOCK

STATE_FILE = Path("bot_state.json")

@dataclass
class SymbolState:
    symbol: str
    score: float = 0.0
    last_backtest: datetime | None = None
    enabled: bool = False

@dataclass
class BotState:
    version: str = "1.0"
    trade_cfg : dict = {
        "lot": 0.01,
        "pip_distance": 200,
        "ratio": "1:2",
        "max_open_trades": 25
    }

    last_backtest_run: datetime | None = None
    next_backtest_run: datetime | None = None
    symbols: Dict[str, SymbolState] = field(default_factory=dict)

    backtest_running: bool = False
    live_trading_enabled: bool = False

    state = Enum("state", ["STARTING", "RUNNING", "RUNNING_BACKTEST", "IDLE", "DEGRADED", "RECOVERING", "STOPPING", "STOPPED"])
class StateManager:
    def __init__(self, manager):
        self._state = manager.Value("state", BotState.state.STARTING.value)

    @staticmethod
    def _dt(d):
        return d.isoformat() if d else None

    @staticmethod
    def _parse_dt(d):
        return datetime.fromisoformat(d) if d else None

    @staticmethod
    def loadBotState() -> BotState:
        with STATE_LOCK:
            if not STATE_FILE.exists():
                return BotState()

            with open(STATE_FILE, "r") as f:
                raw: dict = json.load(f)

            return BotState(
                last_backtest_run=StateManager._parse_dt(raw.get("last_backtest_run"), datetime.now()),
                next_backtest_run=StateManager._parse_dt(raw.get("next_backtest_run"), datetime.now() + timedelta(days=90)),
                symbols={
                    k: SymbolState(
                        symbol=k,
                        score=v["score"],
                        last_backtest=StateManager._parse_dt(v["last_backtest"]),
                        enabled=v["enabled"]
                    )
                    for k, v in raw["symbols"].items()
                },
                backtest_running=raw["backtest_running"],
                live_trading_enabled=raw["live_trading_enabled"],
                
            )

    def set_state(self, state: BotState.state):
        self._state.value = state.value

    def get_state(self) -> BotState:
        return BotState(self._state.value)

    @staticmethod
    def loadSymbolState(sym: str):
        with STATE_LOCK:
            if not STATE_FILE.exists():
                return SymbolState()

            with open(STATE_FILE, "r") as f:
                raw: dict = json.load(f)

            return SymbolState(
                symbol=raw.get(sym, ""),
                score=raw.get("score", 0),
                last_backtest=raw("lastbacktest", datetime.now),
                enabled=False
            )

    @staticmethod
    def saveBotState(state: BotState):
        with STATE_LOCK:
            payload = {
                "last_backtest_run": StateManager._dt(state.last_backtest_run),
                "next_backtest_run": StateManager._dt(state.next_backtest_run),
                "backtest_running": state.backtest_running,
                "live_trading_enabled": state.live_trading_enabled,
                "symbols": {
                    s.symbol: {
                        "score": s.score,
                        "last_backtest": StateManager._dt(s.last_backtest),
                        "enabled": s.enabled
                    }
                    for s in state.symbols.values()
                }
            }

            tmp = STATE_FILE.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(payload, f, indent=2)

            tmp.replace(STATE_FILE)

    @staticmethod
    def saveSymbolstate(state: SymbolState):
        with STATE_LOCK:
            for val in state:
                pass

    @staticmethod
    def update(state: BotState, updates: dict):
        with STATE_LOCK:
            st = getattr(state, state.__qualname__())
            return st

    @staticmethod
    def get_state(state: str):
        return BotState() if isinstance(state, BotState) else SymbolState()

    # core/scheduler.py
    def is_backtest_due(state: BotState) -> bool:
        if state.backtest_running:
            return False

        if not state.next_backtest_run:
            return True

        return datetime.utcnow() >= state.next_backtest_run

    def schedule_next_backtest(state: BotState):
        state.last_backtest_run = datetime.utcnow()
        state.next_backtest_run = state.last_backtest_run + timedelta(days=90)
