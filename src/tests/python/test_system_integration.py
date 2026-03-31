import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from advisor.core.event_bus import EventBus
from advisor.core import events
from advisor.core.state import BotLifecycle, Strategy, SymbolState
from advisor.indicators.signal_store import SignalStore
from advisor.indicators.strategy import StrategyManager
from advisor.Trade.trade_engine import ExecutionProcess


class DummyScheduler:
    async def schedule(
        self,
        process_name,
        required_resources,
        task,
        shutdown_event,
        heartbeats,
        timeout=None,
    ):
        if asyncio.iscoroutinefunction(task):
            return await task()
        result = task()
        if asyncio.iscoroutine(result):
            return await result
        return result


class DummyCache:
    def get(self, _symbol):
        return {"ok": True}


class DummyTelemetry:
    def __init__(self, data_fetch_count: int = 1):
        self.data_fetch_count = data_fetch_count


class DummySymbolWatch:
    def __init__(self, symbols):
        self._symbols = symbols
        self.signals = []
        self.trades = []
        self.errors = []
        self.telemetry = {sym.symbol: DummyTelemetry() for sym in symbols}

    def all_symbol_names(self):
        return [sym.symbol for sym in self._symbols]

    def get(self, symbol: str):
        for sym in self._symbols:
            if sym.symbol == symbol:
                return sym
        return None

    def get_telemetry(self, symbol: str):
        return self.telemetry.get(symbol)

    def mark_signal(self, symbol_or_state):
        symbol = getattr(symbol_or_state, "symbol", symbol_or_state)
        self.signals.append(symbol)

    def mark_trade(self, symbol: str):
        self.trades.append(symbol)

    def mark_error(self, symbol: str, message: str):
        self.errors.append((symbol, message))


class DummyHealthBus:
    def __init__(self):
        self.updates = []

    def update(self, name: str, status: str, payload: dict):
        self.updates.append((name, status, payload))


class DummyStateManager:
    def __init__(self):
        self.bot = SimpleNamespace(state=SimpleNamespace(value=BotLifecycle.RUNNING))

    def set_state(self, state: BotLifecycle):
        self.bot.state = state


class DummyRiskManager:
    def __init__(self, allowed=True, lot=0.1):
        self.allowed = allowed
        self.lot = lot
        self.trade_opens = 0

    def validate(self, _signal):
        return self.allowed, self.lot

    def register_trade_open(self):
        self.trade_opens += 1


class DummyExecutor:
    def __init__(self):
        self.calls = []

    def place_market_order(self, **kwargs):
        self.calls.append(kwargs)
        return "trade-1"


class DummyTradeState:
    def __init__(self):
        self.opened = []

    def register_open(self, trade):
        self.opened.append(trade)


class DummyFrame:
    def __init__(self, value: float):
        self.empty = False
        self._close = DummySeries(value)

    def __getitem__(self, item):
        if item == "close":
            return self._close
        raise KeyError(item)


class DummySeries:
    def __init__(self, value: float):
        self._value = value

    @property
    def iloc(self):
        return [self._value]


async def _wait_for(predicate, timeout=1.0):
    start = datetime.now(timezone.utc)
    while (datetime.now(timezone.utc) - start).total_seconds() < timeout:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.mark.asyncio
async def test_event_flow_market_data_to_execution():
    scheduler = DummyScheduler()
    event_bus = EventBus()
    shutdown_event = asyncio.Event()
    heartbeats = {}
    health_bus = DummyHealthBus()
    cache = DummyCache()
    store = SignalStore()

    strategy = Strategy(
        strategy_name="demo",
        strategy=lambda _flag: {"sig": "bullish", "frame": DummyFrame(1.234)},
    )
    symbol_state = SymbolState(symbol="EURUSD", enabled=True, strategies=[strategy])
    symbol_watch = DummySymbolWatch([symbol_state])

    state_manager = DummyStateManager()

    strategy_mgr = StrategyManager(
        scheduler=scheduler,
        event_bus=event_bus,
        shutdown_event=shutdown_event,
        heartbeats=heartbeats,
        health_bus=health_bus,
        cache_handler=cache,
        symbol_watch=symbol_watch,
        store=store,
        state_manager=state_manager,
    )

    execution = ExecutionProcess(
        client=None,
        signal_store=store,
        health_bus=health_bus,
        heartbeats=heartbeats,
        shutdown_event=shutdown_event,
        scheduler=scheduler,
        state_manager=state_manager,
        symbol_watch=symbol_watch,
        event_bus=event_bus,
        trade_state=DummyTradeState(),
    )
    execution.risk_manager = DummyRiskManager(allowed=True, lot=0.1)
    execution.executor = DummyExecutor()

    executed = []
    event_bus.subscribe(f"{events.ORDER_EXECUTED}:EURUSD", lambda evt: executed.append(evt))

    strategy_mgr.register()
    execution.register()

    await event_bus.publish(events.MARKET_DATA_READY, {"symbols": ["EURUSD"]})
    await event_bus.publish(f"{events.MARKET_DATA_READY}:EURUSD", {"symbol": "EURUSD"})

    done = await _wait_for(lambda: bool(executed))
    assert done is True
    assert "EURUSD" in symbol_watch.signals
    assert "EURUSD" in symbol_watch.trades
