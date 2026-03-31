import pytest

from advisor import MA_DynamAdvisor as main_module
from advisor.core import state as state_module
from advisor.core.state import BotState, SymbolState


class DummyValue:
    def __init__(self, _typecode, value):
        self.value = value


class DummySyncManager:
    def start(self):
        return None

    def Value(self, _typecode, value):
        return DummyValue(_typecode, value)


class DummyHeartbeatRegistry:
    def __init__(self):
        self.beats = {}


class DummyHealthBus:
    def __init__(self):
        self.updates = []

    def update(self, name: str, status: str, payload: dict):
        self.updates.append((name, status, payload))

    def snapshot(self):
        return {}


class DummySupervisor:
    def __init__(self, shutdown_event, manager, state_manager, heartbeats):
        self.shutdown = shutdown_event
        self.manager = manager
        self.state_manager = state_manager
        self.heartbeats = heartbeats
        self.health_bus = DummyHealthBus()
        self.registered = []
        self.start_called = False

    def register_process(self, name, target, *args, depends=None, event_driven=False):
        self.registered.append(
            {"name": name, "depends": depends or [], "event_driven": event_driven, "target": target}
        )
        if event_driven and hasattr(target, "register"):
            target.register()

    def start(self):
        self.start_called = True

    def stop_all(self):
        return None


class DummyDashboard:
    def __init__(self, ctx):
        self.ctx = ctx
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class DummyConfig:
    def __init__(self):
        self.creds = {"server": "demo", "account_id": 1, "password": "pass"}
        self.trade = {"volume": "0.01"}
        self.account = {"Equity": 0}
        self.symbols = {"EURUSD": {"enabled": True}}


class DummyClient:
    def __init__(self):
        self.account_info = {"equity": 1000}

    def initialize(self, _creds, fetch_symbols=False):
        return True

    def get_Symbols(self):
        return ["EURUSD"]


@pytest.mark.asyncio
async def test_program_initializes_and_starts(monkeypatch):
    monkeypatch.setattr(main_module, "SyncManager", DummySyncManager)
    monkeypatch.setattr(main_module, "HeartbeatRegistry", DummyHeartbeatRegistry)
    monkeypatch.setattr(main_module, "Supervisor", DummySupervisor)
    monkeypatch.setattr(main_module, "DashboardServer", DummyDashboard)

    def fake_bootstrap_init(self):
        return {"config": DummyConfig(), "client": DummyClient(), "state": None}

    monkeypatch.setattr(main_module.SystemBootstrap, "initialize", fake_bootstrap_init)

    def fake_load_state():
        return BotState(symbols=[SymbolState(symbol="EURUSD", enabled=True)])

    monkeypatch.setattr(state_module.StateManager, "load_bot_state", staticmethod(fake_load_state))

    bot = main_module.Main()

    async def _noop():
        return None

    monkeypatch.setattr(bot, "_ensure_symbols", _noop)
    monkeypatch.setattr(bot, "_defer_activation_until_backtest", lambda: None)
    monkeypatch.setattr(bot, "_restore_open_positions", lambda: None)

    await bot.initialize()
    bot.start()

    assert bot.dashboard is not None
    assert bot.dashboard.started is True

    supervisor = bot.orch
    assert supervisor.start_called is True

    registry = {entry["name"]: entry for entry in supervisor.registered}
    assert set(registry.keys()) >= {"pipeline", "backtest", "strategy", "execution"}
    assert registry["pipeline"]["event_driven"] is False
    assert registry["backtest"]["event_driven"] is True
    assert registry["strategy"]["event_driven"] is True
    assert registry["execution"]["event_driven"] is True
