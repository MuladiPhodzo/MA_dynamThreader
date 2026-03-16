from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.core.health_bus import HealthBus
from advisor.core.state import StateManager
from advisor.process.process_engine import Supervisor

logger = logging.getLogger("DashboardServer")


class TogglePayload(BaseModel):
    enabled: bool


@dataclass
class DashboardContext:
    supervisor: Supervisor
    state_manager: StateManager
    symbol_watch: SymbolWatch
    health_bus: HealthBus
    backtest_state_file: Path = Path("bot_state.json")


def _serialize_state(state_manager: StateManager) -> dict[str, Any]:
    bot = state_manager.bot
    return {
        "version": bot.version,
        "state": bot.state.name,
        "backtest_running": bot.backtest_running,
        "live_trading_enabled": bot.live_trading_enabled,
        "last_backtest_run": StateManager._serialize_dt(bot.last_backtest_run),
        "next_backtest_run": StateManager._serialize_dt(bot.next_backtest_run),
        "symbols": [
            {
                "symbol": sym.symbol,
                "enabled": sym.enabled,
                "score": sym.score,
                "last_backtest": StateManager._serialize_dt(sym.last_backtest),
            }
            for sym in (bot.symbols or [])
        ],
    }


def status(app: FastAPI):
    ctx = app.state.ctx
    return {
        "health": ctx.health_bus.snapshot(),
        "processes": ctx.supervisor.get_process_snapshot(),
        "telemetry": ctx.symbol_watch.snapshot(),
        "bot_state": _serialize_state(ctx.state_manager),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

def root():
    return {"ok": True, "status": "/status"}

def favicon():
    return Response(status_code=204)

def start_process(app: FastAPI, name: str):
    ctx = app.state.ctx
    ok = ctx.supervisor.start_process(name)
    if not ok:
        raise HTTPException(status_code=404, detail="process not found or dependencies not ready")
    return {"ok": True}

def stop_process(app: FastAPI, name: str):
    ctx = app.state.ctx
    ok = ctx.supervisor.stop_process(name)
    if not ok:
        raise HTTPException(status_code=404, detail="process not found")
    return {"ok": True}

def restart_process(app: FastAPI, name: str):
    ctx = app.state.ctx
    ok = ctx.supervisor.restart_process(name)
    if not ok:
        raise HTTPException(status_code=404, detail="process not found")
    return {"ok": True}

def list_symbols(app: FastAPI):
    ctx = app.state.ctx
    return {"symbols": ctx.symbol_watch.all_symbol_names()}

def toggle_symbol(app: FastAPI, symbol: str, payload: TogglePayload):
    ctx = app.state.ctx
    found = False
    effective_enabled = payload.enabled
    for sym in ctx.state_manager.bot.symbols or []:
        if sym.symbol == symbol:
            if not isinstance(sym.meta, dict):
                sym.meta = {}
            if ctx.state_manager.last_backtest_run is None:
                sym.meta["desired_enabled"] = payload.enabled
                sym.enabled = False
                effective_enabled = False
            else:
                sym.enabled = payload.enabled
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="symbol not found")

    StateManager.save_bot_state(ctx.state_manager.bot)
    ctx.symbol_watch.set_enabled(symbol, effective_enabled)
    return {"ok": True}

def reload_config(app: FastAPI):
    ctx = app.state.ctx
    ctx.state_manager.bot = StateManager.load_bot_state()
    ctx.state_manager.bot.state = ctx.state_manager.get_state()
    ctx.symbol_watch.bot = ctx.state_manager.bot
    ctx.symbol_watch.refresh()
    return {"ok": True}

def run_backtest(app: FastAPI):
    ctx = app.state.ctx
    payload = {"last_backtest": None}
    tmp = ctx.backtest_state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(ctx.backtest_state_file)
    ctx.state_manager.last_backtest_run = None
    return {"ok": True}

def create_app(ctx: DashboardContext) -> FastAPI:
    app = FastAPI(title="MovingAverage Advisor Dashboard API", version="1.0")
    app.state.ctx = ctx

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:4200"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/status")
    def _status():
        return status(app)

    @app.get("/")
    def _root():
        return root()

    @app.get("/favicon.ico")
    def _favicon():
        return favicon()

    @app.post("/processes/{name}/start")
    def _start_process(name: str):
        return start_process(app, name)

    @app.post("/processes/{name}/stop")
    def _stop_process(name: str):
        return stop_process(app, name)

    @app.post("/processes/{name}/restart")
    def _restart_process(name: str):
        return restart_process(app, name)

    @app.get("/symbols")
    def _list_symbols():
        return list_symbols(app)

    @app.post("/symbols/{symbol}/toggle")
    def _toggle_symbol(symbol: str, payload: TogglePayload):
        return toggle_symbol(app, symbol, payload)

    @app.post("/config/reload")
    def _reload_config():
        return reload_config(app)

    @app.post("/backtest/run")
    def _run_backtest():
        return run_backtest(app)

    return app


class DashboardServer:
    def __init__(self, ctx: DashboardContext, host: str = "127.0.0.1", port: int = 8000):
        bound_port = int(os.getenv("DASHBOARD_PORT", port))
        self.app = create_app(ctx)
        self.config = uvicorn.Config(self.app, host=host, port=bound_port, log_level="info")
        self.server = uvicorn.Server(self.config)
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = Thread(target=self.server.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.server.should_exit = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
