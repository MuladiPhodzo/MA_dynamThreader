from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread
from typing import Any
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.core.health_bus import HealthBus
from advisor.core.state import StateManager
from advisor.process.process_engine import Supervisor
from advisor.utils.logging_setup import get_logger

logger = get_logger("DashboardServer")


class TogglePayload(BaseModel):
    enabled: bool


class SupportTicketPayload(BaseModel):
    name: str | None = None
    email: str | None = None
    subject: str
    message: str
    priority: str | None = "normal"

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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _project_root() -> Path:
    root = Path(__file__).resolve()
    for _ in range(6):
        root = root.parent
    return root


def _first_existing(paths: list[Path]) -> Path:
    for path in paths:
        if path.exists():
            return path
    return paths[0]


def _read_stats_history(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with open(path, newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            return []
        rows = rows[-limit:]
        points: list[dict[str, Any]] = []
        for row in rows:
            equity = _safe_float(row.get("balance_after") or row.get("balance_before"))
            points.append(
                {
                    "timestamp": row.get("timestamp") or "",
                    "equity": equity,
                    "balance": equity,
                }
            )
        return points
    except Exception:
        logger.exception("Failed to read trading stats history")
        return []


def _read_trade_log_history(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        rows: list[dict[str, Any]] = []
        if path.suffix == ".jsonl":
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(entry, dict):
                        rows.append(entry)
        else:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                rows = [row for row in data if isinstance(row, dict)]

        if not rows:
            return []

        rows = rows[-limit:]
        points: list[dict[str, Any]] = []
        for row in rows:
            equity = _safe_float(row.get("balance_after") or row.get("balance_before"))
            points.append(
                {
                    "timestamp": row.get("timestamp") or "",
                    "equity": equity,
                    "balance": equity,
                }
            )
        return points
    except Exception:
        logger.exception("Failed to read trades log history")
        return []


def _read_state_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        init_deposit = _safe_float(data.get("init_deposit"))
        last_equity = _safe_float(data.get("last_equity"), init_deposit)
        now = datetime.now(timezone.utc)
        earlier = now - timedelta(days=7)
        return [
            {"timestamp": earlier.isoformat(), "equity": init_deposit, "balance": init_deposit},
            {"timestamp": now.isoformat(), "equity": last_equity, "balance": last_equity},
        ]
    except Exception:
        logger.exception("Failed to read account state history")
        return []


def _build_summary(points: list[dict[str, Any]]) -> dict[str, Any]:
    if not points:
        return {"min": 0, "max": 0, "latest": 0, "change": 0, "change_pct": 0, "count": 0}
    equities = [_safe_float(point.get("equity")) for point in points]
    latest = equities[-1]
    first = equities[0]
    change = latest - first
    change_pct = (change / first * 100) if first else 0
    return {
        "min": min(equities),
        "max": max(equities),
        "latest": latest,
        "change": change,
        "change_pct": change_pct,
        "count": len(points),
    }


def account_history(app: FastAPI, limit: int = 200):
    limit = max(10, min(limit, 2000))
    root = _project_root()
    stats_path = _first_existing(
        [Path("stats/trading_stats.csv"), root / "stats" / "trading_stats.csv"]
    )
    trade_log_path = _first_existing(
        [
            Path("trades/trades_log.jsonl"),
            Path("trades/trades_log.json"),
            root / "trades" / "trades_log.jsonl",
            root / "trades" / "trades_log.json",
        ]
    )
    state_path = _first_existing([Path("bot_state.json"), root / "bot_state.json"])

    points = _read_stats_history(stats_path, limit)
    source = "trading_stats"

    if not points:
        points = _read_trade_log_history(trade_log_path, limit)
        source = "trades_log" if points else source

    if not points:
        points = _read_state_history(state_path)
        source = "state" if points else source

    return {"source": source, "points": points, "summary": _build_summary(points)}


def create_support_ticket(payload: SupportTicketPayload):
    ticket_id = uuid4().hex[:10]
    record = {
        "ticket_id": ticket_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "name": payload.name,
        "email": payload.email,
        "subject": payload.subject,
        "message": payload.message,
        "priority": payload.priority or "normal",
    }
    root = _project_root()
    tickets_path = root / "runtime" / "support_tickets.jsonl"
    tickets_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tickets_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return {"ok": True, "ticket_id": ticket_id}


def support_kb():
    root = _project_root()
    kb_path = root / "runtime" / "support_kb.json"
    if kb_path.exists():
        try:
            with open(kb_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict) and isinstance(data.get("articles"), list):
                return {"articles": data["articles"]}
            if isinstance(data, list):
                return {"articles": data}
        except Exception:
            logger.exception("Failed to read support knowledge base")

    return {
        "articles": [
            {
                "id": "kb-001",
                "title": "Connect the dashboard to the API",
                "summary": "Check the dashboard proxy config and confirm the API is running.",
                "tag": "setup",
            },
            {
                "id": "kb-002",
                "title": "Symbols not updating",
                "summary": "Verify the pipeline process is running and symbols are enabled.",
                "tag": "troubleshooting",
            },
            {
                "id": "kb-003",
                "title": "Backtest stuck in running state",
                "summary": "Restart the backtest process or reload config to reset state.",
                "tag": "operations",
            },
        ]
    }

def _register_basic_routes(app: FastAPI) -> None:
    @app.get("/status")
    def _status():
        return status(app)

    @app.get("/")
    def _root():
        return root()

    @app.get("/favicon.ico")
    def _favicon():
        return favicon()


def _register_process_routes(app: FastAPI) -> None:
    @app.post("/processes/{name}/start")
    def _start_process(name: str):
        return start_process(app, name)

    @app.post("/processes/{name}/stop")
    def _stop_process(name: str):
        return stop_process(app, name)

    @app.post("/processes/{name}/restart")
    def _restart_process(name: str):
        return restart_process(app, name)


def _register_symbol_routes(app: FastAPI) -> None:
    @app.get("/symbols")
    def _list_symbols():
        return list_symbols(app)

    @app.post("/symbols/{symbol}/toggle")
    def _toggle_symbol(symbol: str, payload: TogglePayload):
        return toggle_symbol(app, symbol, payload)


def _register_config_routes(app: FastAPI) -> None:
    @app.post("/config/reload")
    def _reload_config():
        return reload_config(app)

    @app.post("/backtest/run")
    def _run_backtest():
        return run_backtest(app)


def _register_account_routes(app: FastAPI) -> None:
    @app.get("/account/history")
    def _account_history(limit: int = 200):
        return account_history(app, limit)


def _register_support_routes(app: FastAPI) -> None:
    @app.post("/support/ticket")
    def _support_ticket(payload: SupportTicketPayload):
        return create_support_ticket(payload)

    @app.get("/support/kb")
    def _support_kb():
        return support_kb()


def _register_routes(app: FastAPI) -> None:
    _register_basic_routes(app)
    _register_process_routes(app)
    _register_symbol_routes(app)
    _register_config_routes(app)
    _register_account_routes(app)
    _register_support_routes(app)


def create_app(ctx: DashboardContext) -> FastAPI:
    app = FastAPI(title="MovingAverage Advisor Dashboard API", version="1.0")
    app.state.ctx = ctx

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:4200",
            "http://127.0.0.1:4200",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_routes(app)
    return app


class DashboardServer:
    def __init__(self, ctx: DashboardContext, host: str = "127.0.0.1", port: int = 8000):
        bound_host = os.getenv("DASHBOARD_HOST", host)
        bound_port = int(os.getenv("DASHBOARD_PORT", port))
        self.app = create_app(ctx)
        self.config = uvicorn.Config(self.app, host=bound_host, port=bound_port, log_level="info")
        self.server = uvicorn.Server(self.config)
        self._thread: Thread | None = None

    def _run(self) -> None:
        logger.info("Dashboard server starting at http://%s:%s", self.config.host, self.config.port)
        try:
            self.server.run()
        except Exception:
            logger.exception("Dashboard server failed to start")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.server.should_exit = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
