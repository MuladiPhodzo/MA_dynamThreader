from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from advisor.api import server as api_server
from advisor.core.state import BotLifecycle, BotState, SymbolState


class DummySupervisor:
    def __init__(self, names=None):
        self.names = set(names or {"pipeline", "backtest", "strategy", "execution"})

    def get_process_snapshot(self):
        return {
            name: {
                "running": True,
                "pid": 1234,
                "restart_count": 0,
                "last_heartbeat": None,
                "dependencies": [],
            }
            for name in self.names
        }

    def start_process(self, name: str) -> bool:
        return name in self.names

    def stop_process(self, name: str) -> bool:
        return name in self.names

    def restart_process(self, name: str) -> bool:
        return name in self.names


class DummySymbolWatch:
    def __init__(self, symbols):
        self._symbols = symbols
        self.enabled = {}
        self.refreshed = False

    def all_symbol_names(self):
        return [sym.symbol for sym in self._symbols]

    def snapshot(self):
        return {sym.symbol: {"enabled": sym.enabled} for sym in self._symbols}

    def set_enabled(self, symbol: str, enabled: bool):
        self.enabled[symbol] = enabled

    def refresh(self):
        self.refreshed = True


class DummyHealthBus:
    def snapshot(self):
        return {}


@dataclass
class DummyStateManager:
    bot: BotState

    def get_state(self):
        return self.bot.state


def _make_ctx(tmp_path: Path, *, last_backtest_run=None):
    bot = BotState(
        symbols=[SymbolState(symbol="EURUSD", enabled=True, score=0.9)],
        state=BotLifecycle.RUNNING,
    )
    bot.last_backtest_run = last_backtest_run
    state_manager = DummyStateManager(bot=bot)
    symbol_watch = DummySymbolWatch(bot.symbols)
    ctx = api_server.DashboardContext(
        supervisor=DummySupervisor(),
        state_manager=state_manager,
        symbol_watch=symbol_watch,
        health_bus=DummyHealthBus(),
        backtest_state_file=tmp_path / "bot_state.json",
    )
    return ctx, symbol_watch, state_manager


def test_status_and_symbols(tmp_path):
    ctx, _symbol_watch, _state = _make_ctx(tmp_path)
    app = api_server.create_app(ctx)
    client = TestClient(app)

    res = client.get("/status")
    assert res.status_code == 200
    payload = res.json()
    assert "health" in payload
    assert "processes" in payload
    assert "telemetry" in payload
    assert payload["bot_state"]["symbols"]

    res = client.get("/symbols")
    assert res.status_code == 200
    assert res.json()["symbols"] == ["EURUSD"]


def test_process_controls(tmp_path):
    ctx, _symbol_watch, _state = _make_ctx(tmp_path)
    app = api_server.create_app(ctx)
    client = TestClient(app)

    assert client.post("/processes/pipeline/start").status_code == 200
    assert client.post("/processes/pipeline/stop").status_code == 200
    assert client.post("/processes/pipeline/restart").status_code == 200
    assert client.post("/processes/unknown/start").status_code == 404


def test_toggle_symbol_and_reload_config(tmp_path, monkeypatch):
    ctx, symbol_watch, state_manager = _make_ctx(tmp_path, last_backtest_run=None)
    app = api_server.create_app(ctx)
    client = TestClient(app)

    monkeypatch.setattr(api_server.StateManager, "save_bot_state", staticmethod(lambda _state: None))
    monkeypatch.setattr(
        api_server.StateManager,
        "load_bot_state",
        staticmethod(lambda: BotState(symbols=[SymbolState(symbol="EURUSD", enabled=False)])),
    )

    res = client.post("/symbols/EURUSD/toggle", json={"enabled": True})
    assert res.status_code == 200
    assert symbol_watch.enabled["EURUSD"] is False

    res = client.post("/config/reload")
    assert res.status_code == 200
    assert symbol_watch.refreshed is True
    assert state_manager.bot.symbols


def test_run_backtest_support_and_history(tmp_path, monkeypatch):
    ctx, _symbol_watch, _state = _make_ctx(tmp_path, last_backtest_run=datetime.now(timezone.utc))

    monkeypatch.setattr(api_server, "_project_root", lambda: tmp_path)

    app = api_server.create_app(ctx)
    client = TestClient(app)

    res = client.post("/backtest/run")
    assert res.status_code == 200
    assert ctx.backtest_state_file.exists()

    res = client.get("/support/kb")
    assert res.status_code == 200
    assert res.json()["articles"]

    res = client.post(
        "/support/ticket",
        json={"name": "Test", "email": "t@example.com", "subject": "Help", "message": "Hi"},
    )
    assert res.status_code == 200
    tickets = tmp_path / "runtime" / "support_tickets.jsonl"
    assert tickets.exists()
    line = tickets.read_text(encoding="utf-8").strip()
    assert line
    record = json.loads(line)
    assert record["subject"] == "Help"

    res = client.get("/account/history")
    assert res.status_code == 200
    payload = res.json()
    assert "summary" in payload
    assert "points" in payload
