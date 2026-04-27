from datetime import datetime, timedelta, timezone
from threading import Event

import pandas as pd
import pytest

from advisor.core import dependency_graph, events
from advisor.core.portfolio.portfolio_manager import PortfolioManager
from advisor.core.state import BotState, BotLifecycle, Strategy, SymbolState
from Strategy_model.signals.signal_store import SignalStore
from Strategy_model.strategy_runner import StrategyManager
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


class DummyRegistry:
    def __init__(self):
        self.names = []

    def register(self, name: str):
        self.names.append(name)


class DummySymbolWatch:
    def __init__(self, symbols=None):
        self._symbols = symbols or []
        self.signals = []
        self.trades = []
        self.errors = []
        self.refreshed = False

    def all_symbol_names(self):
        return [sym.symbol for sym in self._symbols]

    def get(self, symbol: str):
        for sym in self._symbols:
            if sym.symbol == symbol:
                return sym
        return None

    def snapshot(self):
        return {sym.symbol: {"enabled": sym.enabled} for sym in self._symbols}

    def refresh(self):
        self.refreshed = True

    def mark_signal(self, symbol: str):
        self.signals.append(symbol)

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
        registry=DummyRegistry(),
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
        registry=DummyRegistry(),
    )

    process._apply_backtest_scores()

    assert bot.symbols[0].enabled is True
    assert bot.symbols[1].enabled is False
    assert symbol_watch.refreshed is True
    assert saved, "Expected bot state to be persisted after enabling symbols"


def test_backtest_parse_strategy_event_runs_top20(monkeypatch):
    monkeypatch.setattr(backtest_engine, "Backtest", DummyBacktest)
    bot = BotState()
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
        registry=DummyRegistry(),
    )

    parsed = process._parse_event(
        {
            "type": f"{events.RUN_BACKTEST}:BreakoutFlow",
            "strategy_name": "BreakoutFlow",
            "top_n": 20,
        }
    )

    assert parsed is not None
    strategy_name, symbol, payload = parsed
    assert strategy_name == "BreakoutFlow"
    assert symbol is None
    assert payload["top_n"] == 20


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


def test_strategy_manager_create_strategy_broadcasts_top20_backtest():
    class DummyStrategyRegistry:
        def __init__(self):
            self.upserts = []
            self.refreshed = False

        def refresh_configs(self, persist=False):
            self.refreshed = True
            return {}

        def upsert_config(self, name, config, overwrite=True):
            self.upserts.append((name, config, overwrite))
            return False, dict(config)

    event_bus = DummyEventBus()
    health_bus = DummyHealthBus()
    manager = StrategyManager(
        scheduler=DummyScheduler(),
        event_bus=event_bus,
        shutdown_event=Event(),
        heartbeats={},
        health_bus=health_bus,
        cache_handler=type("Cache", (), {"get": lambda *_args, **_kwargs: {"ok": True}})(),
        symbol_watch=DummySymbolWatch(),
        store=SignalStore(),
        state_manager=DummyStateManager(BotState()),
    )
    manager.strategy_registry = DummyStrategyRegistry()

    wrapper = manager.create_strategy(
        config={"name": "BreakoutFlow", "rules": {"min_score": 0.72}},
        source="test",
        emit_backtest=True,
    )

    assert wrapper is not None
    assert wrapper.strategy_name == "BreakoutFlow"
    assert manager.strategy_registry.upserts[0][0] == "BreakoutFlow"
    assert manager.strategy_registry.upserts[0][1]["rules"]["min_score"] == 0.72
    assert event_bus.published
    event_name, payload = event_bus.published[0]
    assert event_name == f"{events.RUN_BACKTEST}:BreakoutFlow"
    assert payload["strategy_name"] == "BreakoutFlow"
    assert payload["top_n"] == 20


@pytest.mark.asyncio
async def test_strategy_manager_uses_integrated_strategy_from_cache(monkeypatch):
    class DummyIntegratedStrategy:
        def __init__(self, data):
            self.data = data

        def run(self):
            frame = pd.DataFrame({"close": [1.234]})
            return {
                "sig": "bullish",
                "frame": frame,
                "confidence": 88.0,
                "metadata": {"score": 0.91},
            }

    monkeypatch.setattr("Strategy_model.strategy_runner.OrchestratedStrategy", DummyIntegratedStrategy)

    cache = type(
        "Cache",
        (),
        {
            "get": lambda *_args, **_kwargs: {
                "15M": pd.DataFrame({"open": [1.2], "high": [1.3], "low": [1.1], "close": [1.234]}),
                "1H": pd.DataFrame({"open": [1.2], "high": [1.3], "low": [1.1], "close": [1.234]}),
                "4H": pd.DataFrame({"open": [1.2], "high": [1.3], "low": [1.1], "close": [1.234]}),
                "1D": pd.DataFrame({"open": [1.2], "high": [1.3], "low": [1.1], "close": [1.234]}),
            }
        },
    )()
    event_bus = DummyEventBus()
    store = SignalStore()
    state = DummyStateManager(BotState())
    symbol_watch = DummySymbolWatch([SymbolState(symbol="EURUSD", enabled=True, strategies=[])])

    manager = StrategyManager(
        scheduler=DummyScheduler(),
        event_bus=event_bus,
        shutdown_event=Event(),
        heartbeats={},
        health_bus=DummyHealthBus(),
        cache_handler=cache,
        symbol_watch=symbol_watch,
        store=store,
        state_manager=state,
    )

    await manager._run_symbol("EURUSD")

    latest = store.get_latest("EURUSD")
    assert latest is not None
    assert latest.side == "buy"
    assert latest.confidence == 88.0


def test_strategy_manager_integrated_strategy_smoke():
    def _frame():
        idx = pd.date_range("2026-01-01", periods=260, freq="15min", tz="UTC")
        closes = (pd.Series(range(260), dtype=float) * 0.001 + 1.10).to_numpy()
        return pd.DataFrame(
            {
                "time": idx,
                "open": closes - 0.0005,
                "high": closes + 0.0008,
                "low": closes - 0.0008,
                "close": closes,
            }
        )

    cache = type(
        "Cache",
        (),
        {
            "get": lambda *_args, **_kwargs: {
                "15M": _frame(),
                "1H": _frame(),
                "4H": _frame(),
                "1D": _frame(),
            }
        },
    )()
    manager = StrategyManager(
        scheduler=DummyScheduler(),
        event_bus=DummyEventBus(),
        shutdown_event=Event(),
        heartbeats={},
        health_bus=DummyHealthBus(),
        cache_handler=cache,
        symbol_watch=DummySymbolWatch([SymbolState(symbol="EURUSD", enabled=True, strategies=[])]),
        store=SignalStore(),
        state_manager=DummyStateManager(BotState()),
    )

    payload = manager._build_orchestrated_signal("EURUSD")

    assert payload is None or payload["symbol"] == "EURUSD"


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
    symbol_watch = DummySymbolWatch([SymbolState(symbol="EURUSD", enabled=True)])

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


@pytest.mark.asyncio
async def test_execution_process_prefers_higher_ranked_symbol():
    store = SignalStore()
    now = datetime.now(timezone.utc)
    store.add_signal(
        {
            "id": "EURUSD:1",
            "symbol": "EURUSD",
            "side": "buy",
            "sl": 0.001,
            "tp": 0.002,
            "confidence": 40.0,
            "data": {"score": 0.4},
            "timestamp": now,
        }
    )
    store.add_signal(
        {
            "id": "GBPUSD:1",
            "symbol": "GBPUSD",
            "side": "sell",
            "sl": 0.001,
            "tp": 0.002,
            "confidence": 90.0,
            "data": {"score": -0.9},
            "timestamp": now,
        }
    )

    event_bus = DummyEventBus()
    symbol_watch = DummySymbolWatch(
        [
            SymbolState(symbol="EURUSD", enabled=True),
            SymbolState(symbol="GBPUSD", enabled=True),
        ]
    )

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
        portfolio_manager=PortfolioManager(capital=10000, max_positions=1),
    )

    process.risk_manager = DummyRiskManager(allowed=True, lot=0.1)
    process.executor = DummyExecutor(trade="trade-portfolio")

    await process._execute_symbol("EURUSD", {"id": "EURUSD:1", "symbol": "EURUSD"})
    await process._execute_symbol("GBPUSD", {"id": "GBPUSD:1", "symbol": "GBPUSD"})

    assert symbol_watch.trades == ["GBPUSD"]
    assert process.executor.calls[0]["symbol"] == "GBPUSD"
