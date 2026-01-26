# core/state.py

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List
# core/state.py

import json
from pathlib import Path
from core.locks import STATE_LOCK

STATE_FILE = Path("bot_state.json")

@dataclass
class SymbolState:
    symbol: str
    win_rate: float = 0.0
    score: float = 0.0
    last_backtest: datetime | None = None
    enabled: bool = False

@dataclass
class BotState:
    version: str = "1.0"

    last_backtest_run: datetime | None = None
    next_backtest_run: datetime | None = None

    symbols: Dict[str, SymbolState] = field(default_factory=dict)

    backtest_running: bool = False
    live_trading_enabled: bool = False

class BotStateManager:
    def _dt(d):
        return d.isoformat() if d else None

    def _parse_dt(d):
        return datetime.fromisoformat(d) if d else None

    @staticmethod
    def load() -> BotState:
        with STATE_LOCK:
            if not STATE_FILE.exists():
                return BotState()

            with open(STATE_FILE, "r") as f:
                raw = json.load(f)

            return BotState(
                last_backtest_run=_parse_dt(raw.get("last_backtest_run")),
                next_backtest_run=_parse_dt(raw.get("next_backtest_run")),
                symbols={
                    k: SymbolState(
                        symbol=k,
                        win_rate=v["win_rate"],
                        score=v["score"],
                        last_backtest=_parse_dt(v["last_backtest"]),
                        enabled=v["enabled"]
                    )
                    for k, v in raw["symbols"].items()
                },
                backtest_running=raw["backtest_running"],
                live_trading_enabled=raw["live_trading_enabled"]
            )

    @staticmethod
    def save(state: BotState):
        with STATE_LOCK:
            payload = {
                "last_backtest_run": _dt(state.last_backtest_run),
                "next_backtest_run": _dt(state.next_backtest_run),
                "backtest_running": state.backtest_running,
                "live_trading_enabled": state.live_trading_enabled,
                "symbols": {
                    s.symbol: {
                        "win_rate": s.win_rate,
                        "score": s.score,
                        "last_backtest": _dt(s.last_backtest),
                        "enabled": s.enabled
                    }
                    for s in state.symbols.values()
                }
            }

            tmp = STATE_FILE.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(payload, f, indent=2)

            tmp.replace(STATE_FILE)

