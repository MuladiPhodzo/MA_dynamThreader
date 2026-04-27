import asyncio
from threading import Event

import pytest

from advisor.core import events
from advisor.core.state import BotState, SymbolState, symbolCycle
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.mt5_pipeline import core as pipeline_core
from advisor.mt5_pipeline import runner as pipeline_runner


class DummyTelemetry:
    def __init__(self, error_count: int = 0):
        self.error_count = error_count
        self.last_error = None
        self.last_error_time = None


class DummySymbolWatch:
    def __init__(self):
        self.active_symbols = ["EURUSD"]
        self._telemetry = {"EURUSD": DummyTelemetry()}

    def active_symbol_names(self):
        return ["EURUSD"]

    def all_symbol_names(self):
        return ["EURUSD"]

    def snapshot(self):
        return {"EURUSD": {"enabled": True}}

    def get_telemetry(self, symbol: str):
        return self._telemetry.get(symbol)

    def refresh(self):
        return None


class DummyHealthBus:
    def __init__(self):
        self.updates = []

    def update(self, name: str, status: str, payload: dict):
        self.updates.append((name, status, payload))


class DummyEventBus:
    def __init__(self):
        self.subscribed = []
        self.emitted = []
        self.published = []

    def subscribe(self, *args, **kwargs):
        self.subscribed.append((args, kwargs))
        return None

    def emit(self, event_type: str, payload=None):
        self.emitted.append((event_type, payload))

    async def publish(self, event_type: str, payload=None):
        self.published.append((event_type, payload))


class DummyScheduler:
    def __init__(self):
        self.calls = []

    async def schedule(
        self,
        process_name: str,
        required_resources,
        task,
        shutdown_event,
        heartbeats,
        timeout=None,
    ):
        self.calls.append(
            {
                "process_name": process_name,
                "required_resources": required_resources,
                "shutdown_event": shutdown_event,
                "heartbeats": heartbeats,
                "timeout": timeout,
            }
        )
        if asyncio.iscoroutinefunction(task):
            return await task()
        result = task()
        if asyncio.iscoroutine(result):
            return await result
        return result


class DummyStateManager:
    def __init__(self):
        self.bot = type("Bot", (), {"symbols": []})()

    def set_state(self, *_args, **_kwargs):
        return None


class FakePipeline:
    def __init__(self, client, cache_handler, symbol_watch, state_manager):
        self.client = client
        self.cache = cache_handler
        self.symbol_watch = symbol_watch
        self.state_manager = state_manager
        self.called = False

    async def run_once(self, on_symbol=None, per_symbol_timeout=None, max_concurrent=None):
        self.called = True
        if on_symbol is not None:
            for symbol in self.symbol_watch.active_symbol_names():
                on_symbol(symbol, True)


class CountingClient:
    def __init__(self):
        self.calls = []

    def get_multi_tf_data(self, symbol: str, backtest: bool = False):
        self.calls.append((symbol, backtest))
        return {"15M": [1, 2, 3]}


class DummyCache:
    def __init__(self):
        self.saved = []

    def set_atomic(self, symbol: str, data):
        self.saved.append((symbol, data))


def _build_pipeline(monkeypatch):
    monkeypatch.setattr(pipeline_runner.core, "MarketDataPipeline", FakePipeline)
    event_bus = DummyEventBus()
    scheduler = DummyScheduler()
    symbol_watch = DummySymbolWatch()
    pipeline = pipeline_runner.pipelineProcess(
        client=None,
        cache_handler=None,
        shutdown_event=Event(),
        heartbeats={},
        health_bus=DummyHealthBus(),
        scheduler=scheduler,
        state_manager=DummyStateManager(),
        symbol_watch=symbol_watch,
        event_bus=event_bus,
    )
    return pipeline, event_bus, scheduler


def test_pipeline_register_is_noop(monkeypatch):
    pipeline, event_bus, _scheduler = _build_pipeline(monkeypatch)
    pipeline.register()
    assert event_bus.subscribed == []


@pytest.mark.asyncio
async def test_pipeline_poll_cycle_publishes_market_data(monkeypatch):
    pipeline, event_bus, scheduler = _build_pipeline(monkeypatch)

    await pipeline._run_poll_cycle()

    assert scheduler.calls, "Expected scheduler.schedule to be called"
    assert any(
        event_type == events.MARKET_DATA_READY for event_type, _payload in event_bus.published
    ), "Expected MARKET_DATA_READY publish after polling"


@pytest.mark.asyncio
async def test_market_data_pipeline_skips_symbols_with_active_backtests():
    client = CountingClient()
    cache = DummyCache()
    bot = BotState(
        symbols=[
            SymbolState(symbol="EURUSD", enabled=True, state=symbolCycle.READY),
            SymbolState(symbol="GBPUSD", enabled=True, state=symbolCycle.BACKTESTING),
        ]
    )
    symbol_watch = SymbolWatch(bot)
    pipeline = pipeline_core.MarketDataPipeline(
        client=client,
        cache_handler=cache,
        symbol_watch=symbol_watch,
        state_manager=DummyStateManager(),
    )

    await pipeline.run_once()

    assert client.calls == [("EURUSD", True)]
    assert [symbol for symbol, _data in cache.saved] == ["EURUSD"]


def test_symbol_watch_excludes_backtesting_symbols_from_ingestion():
    bot = BotState(
        symbols=[
            SymbolState(symbol="EURUSD", enabled=True, state=symbolCycle.READY),
            SymbolState(symbol="GBPUSD", enabled=True, state=symbolCycle.INITIALIZING),
            SymbolState(symbol="USDJPY", enabled=False, state=symbolCycle.BACKTESTING),
        ]
    )
    symbol_watch = SymbolWatch(bot)

    assert symbol_watch.ingestible_symbol_names() == ["EURUSD"]
    assert symbol_watch.ingestible_symbol_names(include_all=True) == ["EURUSD"]
