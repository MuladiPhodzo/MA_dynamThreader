from datetime import datetime, timedelta, timezone
from threading import Event

import pytest

from advisor.core import dependency_graph, events
from advisor.core.state import BotState, BotLifecycle, Strategy, SymbolState
from advisor.indicators.signal_store import SignalStore
from advisor.indicators.strategy import StrategyManager
from advisor.backtest import engine as backtest_engine
from advisor.Trade.trade_engine import ExecutionProcess


class DummyHealthBus:
    def __init__(self):
        self.updates = []

    def update(self, name: str, status: str, payload: dict):
        self.updates.append((name, status, payload))


class DummyEventBus:
    def __init__(self):
        self.published = []

    def subscribe(self, *args, **kwargs):
        return None

    async def publish(self, event_type: str, payload=None):
        self.published.append((event_type, payload))


class DummyScheduler:
    async def schedule(self, *args, **kwargs):
        return None


class DummySymbolWatch:
    def __init__(self, symbols=None):
        self._symbols = symbols or []
        self.trades = []
        self.errors = []
        self.refreshed = False

    def all_symbol_names(self):
        return [sym.symbol for sym in self._symbols]

    def snapshot(self):
        return {sym.symbol: {"enabled": sym.enabled} for sym in self._symbols}

    def refresh(self):
        self.refreshed = True

    def mark_trade(self, symbol: str):
        self.trades.append(symbol)

    def mark_error(self, symbol: str, message: str):
        self.errors.append((symbol, message))


class DummyStateManager:
    def __init__(self, bot: BotState):
        self.bot = bot
        self.last_backtest_run = bot.last_backtest_run
        self._state = BotLifecycle.RUNNING

    def set_state(self, state: BotLifecycle):
        self._state = state
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
    def __init__(self, trade="trade-1"):
        self.trade = trade
        self.calls = []

    def place_market_order(self, **kwargs):
        self.calls.append(kwargs)
        return self.trade


class DummyTradeState:
    def __init__(self):
        self.opened = []

    def register_open(self, trade):
        self.opened.append(trade)


class DummyBacktest:
    def __init__(self, client, cache_handler, symbol_watch):
        self.client = client
        self.cache = cache_handler
        self.symbol_watch = symbol_watch

    def initialise(self):
        return None

    async def run_once(self, *args, **kwargs):
        return None


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


def test_dependency_graph_orders_pipeline_chain():
    graph = dependency_graph.DependencyGraph()
    graph.add("pipeline", [])
    graph.add("backtest", ["pipeline"])
    graph.add("strategy", ["pipeline", "backtest"])
    graph.add("execution", ["strategy"])

    order = graph.resolve_order()
    assert order.index("pipeline") < order.index("backtest") < order.index("strategy") < order.index("execution")


def test_backtest_should_run_based_on_last_run(monkeypatch):
    monkeypatch.setattr(backtest_engine, "Backtest", DummyBacktest)
    bot = BotState()
    bot.last_backtest_run = datetime.now(timezone.utc) - timedelta(days=100)
    state = DummyStateManager(bot)

    process = backtest_engine.BacktestProcess(
        client=None,
        cache_handler=None,
        scheduler=DummyScheduler(),
        health_bus=DummyHealthBus(),
        heartbeats={},
        shutdown_event=Event(),
        state_manager=state,
        symbol_watch=DummySymbolWatch(bot.symbols),
        event_bus=DummyEventBus(),
    )

    assert process._should_run() is True
    state.last_backtest_run = datetime.now(timezone.utc)
    assert process._should_run() is False


def test_backtest_apply_scores_enables_symbols(monkeypatch):
    monkeypatch.setattr(backtest_engine, "Backtest", DummyBacktest)

    bot = BotState()
    bot.symbols = [
        SymbolState(symbol="EURUSD", score=0.8, enabled=False),
        SymbolState(symbol="GBPUSD", score=0.3, enabled=False),
    ]
    state = DummyStateManager(bot)
    symbol_watch = DummySymbolWatch(bot.symbols)

    saved = []
    monkeypatch.setattr(backtest_engine.StateManager, "save_bot_state", lambda _state: saved.append(True))

    process = backtest_engine.BacktestProcess(
        client=None,
        cache_handler=None,
        scheduler=DummyScheduler(),
        health_bus=DummyHealthBus(),
        heartbeats={},
        shutdown_event=Event(),
        state_manager=state,
        symbol_watch=symbol_watch,
        event_bus=DummyEventBus(),
    )

    process._apply_backtest_scores()

    assert bot.symbols[0].enabled is True
    assert bot.symbols[1].enabled is False
    assert symbol_watch.refreshed is True
    assert saved, "Expected bot state to be persisted after enabling symbols"


def test_strategy_build_signal_accepts_bullish():
    manager = StrategyManager(
        scheduler=DummyScheduler(),
        event_bus=DummyEventBus(),
        shutdown_event=Event(),
        heartbeats={},
        health_bus=DummyHealthBus(),
        cache_handler=type("Cache", (), {"get": lambda *_args, **_kwargs: {"ok": True}})(),
        symbol_watch=DummySymbolWatch(),
        store=SignalStore(),
        state_manager=DummyStateManager(BotState()),
    )

    frame = DummyFrame(1.234)
    strat = Strategy(strategy_name="demo", strategy=lambda _flag: {"sig": "bullish", "frame": frame})

    payload = manager._build_signal("EURUSD", strat)
    assert payload is not None
    assert payload["side"] == "buy"
    assert payload["symbol"] == "EURUSD"


def test_strategy_build_signal_filters_weak():
    manager = StrategyManager(
        scheduler=DummyScheduler(),
        event_bus=DummyEventBus(),
        shutdown_event=Event(),
        heartbeats={},
        health_bus=DummyHealthBus(),
        cache_handler=type("Cache", (), {"get": lambda *_args, **_kwargs: {"ok": True}})(),
        symbol_watch=DummySymbolWatch(),
        store=SignalStore(),
        state_manager=DummyStateManager(BotState()),
    )

    frame = DummyFrame(1.234)
    strat = Strategy(strategy_name="demo", strategy=lambda _flag: {"sig": "(W) bullish", "frame": frame})

    payload = manager._build_signal("EURUSD", strat)
    assert payload is None


@pytest.mark.asyncio
async def test_execution_process_executes_trade(monkeypatch):
    store = SignalStore()
    now = datetime.now(timezone.utc)
    store.add_signal(
        {
            "id": "EURUSD:1",
            "symbol": "EURUSD",
            "side": "buy",
            "sl": 0.001,
            "tp": 0.002,
            "timestamp": now,
        }
    )

    event_bus = DummyEventBus()
    symbol_watch = DummySymbolWatch()

    process = ExecutionProcess(
        client=None,
        signal_store=store,
        health_bus=DummyHealthBus(),
        heartbeats={},
        shutdown_event=Event(),
        scheduler=DummyScheduler(),
        state_manager=DummyStateManager(BotState()),
        symbol_watch=symbol_watch,
        event_bus=event_bus,
        trade_state=DummyTradeState(),
    )

    process.risk_manager = DummyRiskManager(allowed=True, lot=0.1)
    process.executor = DummyExecutor(trade="trade-123")

    await process._execute_symbol("EURUSD", {"id": "EURUSD:1"})

    assert "EURUSD" in symbol_watch.trades
    assert any(
        evt_type == f"{events.ORDER_EXECUTED}:EURUSD" for evt_type, _payload in event_bus.published
    )
