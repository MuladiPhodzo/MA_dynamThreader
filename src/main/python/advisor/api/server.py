from __future__ import annotations

import csv
import copy
import inspect
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
from advisor.core.event_bus import EventBus
from advisor.core import events
from advisor.Strategy_model.Fundamentals.technical_registry import TechnicalRegistry
from advisor.Strategy_model.indicators.registry import IndicatorRegistry, load_builtin_indicators
from advisor.Strategy_model.patterns.pattern_registry import PatternRegistry
from advisor.Strategy_model.strategy import StrategyModel
from advisor.Strategy_model.strategy_registry import StrategyRegistry
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


class BacktestTriggerPayload(BaseModel):
    symbol: str | None = None
    strategy_name: str | None = None
    strategy: str | None = None


class StrategyCreatePayload(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    overwrite: bool = True

@dataclass
class DashboardContext:
    supervisor: Supervisor
    state_manager: StateManager
    symbol_watch: SymbolWatch
    health_bus: HealthBus
    event_bus: EventBus | None = None
    client: Any | None = None
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


def _last_backtest_run(state_manager: StateManager) -> datetime | None:
    value = getattr(state_manager, "last_backtest_run", None)
    if value is not None:
        return value
    bot = getattr(state_manager, "bot", None)
    return getattr(bot, "last_backtest_run", None)


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
    last_backtest_run = _last_backtest_run(ctx.state_manager)
    for sym in ctx.state_manager.bot.symbols or []:
        if sym.symbol == symbol:
            if not isinstance(sym.meta, dict):
                sym.meta = {}
            if last_backtest_run is None:
                sym.meta["enabled_before_backtest"] = payload.enabled
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
    if ctx.event_bus is not None:
        ctx.event_bus.emit(events.STRATEGY_CONFIG_UPDATED, {"action": "reload"})
    return {"ok": True}

def run_backtest(app: FastAPI, payload: BacktestTriggerPayload | None = None):
    ctx = app.state.ctx
    request = payload or BacktestTriggerPayload()
    symbol = (request.symbol or "").strip()
    strategy_name = (request.strategy_name or request.strategy or "").strip()

    if symbol:
        event_strategy = strategy_name or StrategyModel.DEFAULT_CONFIG.get("name", "EMA_Proxim8te")
        event_type = f"{events.RUN_BACKTEST}:{event_strategy}:{symbol}"

        emitted = False
        if ctx.event_bus is not None:
            ctx.event_bus.emit(
                event_type,
                {
                    "symbol": symbol,
                    "strategy_name": event_strategy,
                    "strategy": event_strategy,
                },
            )
            emitted = True

        return {
            "ok": True,
            "mode": "targeted",
            "event": event_type,
            "symbol": symbol,
            "strategy_name": event_strategy,
            "emitted": emitted,
        }

    ctx.state_manager.last_backtest_run = None
    ctx.state_manager.bot.next_backtest_run = None
    ctx.state_manager.bot.backtest_running = False
    for sym in ctx.state_manager.bot.symbols or []:
        sym.last_backtest = None
        if isinstance(sym.meta, dict):
            for key in (
                "last_backtest_at",
                "last_backtest_strategies",
                "last_backtest_request",
                "strategy_backtests",
            ):
                sym.meta.pop(key, None)
    StateManager.save_bot_state(ctx.state_manager.bot)
    return {"ok": True, "mode": "reset"}


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _strategy_template() -> dict[str, Any]:
    return copy.deepcopy(StrategyModel.DEFAULT_CONFIG)


def _normalize_strategy_name(name: str | None, config: dict[str, Any] | None = None) -> str:
    candidate = (name or "").strip()
    if not candidate and isinstance(config, dict):
        candidate = str(config.get("name") or "").strip()
    return candidate or StrategyModel.DEFAULT_CONFIG.get("name", "EMA_Proxim8te")


def _normalize_strategy_config(name: str, config: dict[str, Any] | None) -> dict[str, Any]:
    merged = _deep_merge_dict(_strategy_template(), config or {})
    merged["name"] = name

    tools = merged.get("tools")
    if isinstance(tools, dict):
        technical = tools.get("technical")
        if isinstance(technical, dict):
            items = technical.get("tools")
            if isinstance(items, str):
                items = [item.strip() for item in items.split(",") if item and item.strip()]
            elif isinstance(items, list):
                items = [str(item).strip() for item in items if str(item).strip()]
            else:
                items = []
            technical["tools"] = items

        patterns = tools.get("patterns")
        if isinstance(patterns, dict):
            values = patterns.get("patterns")
            if isinstance(values, str):
                values = [item.strip() for item in values.split(",") if item and item.strip()]
            elif isinstance(values, list):
                values = [str(item).strip() for item in values if str(item).strip()]
            else:
                values = []
            patterns["patterns"] = values

    return merged


def create_strategy(app: FastAPI, payload: StrategyCreatePayload):
    ctx = app.state.ctx
    name = _normalize_strategy_name(payload.name, payload.config)
    compiled = _normalize_strategy_config(name, payload.config)
    registry = StrategyRegistry(root_path=_project_root())
    try:
        existed, stored = registry.upsert_config(name, compiled, overwrite=payload.overwrite)
    except ValueError as exc:
        text = str(exc)
        if "already exists" in text:
            raise HTTPException(status_code=409, detail=text) from exc
        raise HTTPException(status_code=400, detail=text) from exc

    registry.refresh_configs(persist=True)

    backtest_event = f"{events.RUN_BACKTEST}:{name}"
    backtest_payload = {
        "type": backtest_event,
        "strategy_name": name,
        "strategy": name,
        "config": stored,
        "top_n": 20,
        "source": "api.create_strategy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    backtest_emitted = False
    if ctx.event_bus is not None:
        ctx.event_bus.emit(events.STRATEGY_CONFIG_UPDATED, {"name": name, "action": "upsert"})
        ctx.event_bus.emit(events.STRATEGY_REGISTRY_UPDATED, {"name": name, "action": "upsert"})
        ctx.event_bus.emit(backtest_event, backtest_payload)
        backtest_emitted = True

    return {
        "ok": True,
        "name": name,
        "overwrite": bool(existed),
        "backtest_event": backtest_event,
        "backtest_emitted": backtest_emitted,
        "config_path": str(registry.config_path),
        "registry_path": str(registry.registry_path),
        "config": stored,
    }


def strategy_registry_snapshot(app: FastAPI):
    registry = StrategyRegistry(root_path=_project_root())
    return registry.snapshot()


def strategy_registry_list(app: FastAPI):
    registry = StrategyRegistry(root_path=_project_root())
    return {"strategies": registry.list_strategies()}


def _split_identifier(value: str) -> str:
    text = str(value or "").replace("_", " ").replace("-", " ").strip()
    if not text:
        return ""
    if text.isupper():
        return text
    words: list[str] = []
    current = ""
    for index, ch in enumerate(text):
        next_ch = text[index + 1] if index + 1 < len(text) else ""
        starts_word = ch.isupper() and (
            bool(current)
            and (
                not current[-1].isupper()
                or (bool(next_ch) and next_ch.islower())
            )
        )
        if starts_word:
            words.append(current)
            current = ch
        else:
            current += ch
    if current:
        words.append(current)
    return " ".join(word if word.isupper() else word.title() for word in words)


def _serializable_default(value: Any) -> Any:
    if value is inspect.Parameter.empty:
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _constructor_defaults(cls: type) -> dict[str, Any]:
    try:
        signature = inspect.signature(cls.__init__)
    except (TypeError, ValueError):
        return {}

    params: dict[str, Any] = {}
    for name, param in signature.parameters.items():
        if name == "self" or param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        if param.default is inspect.Parameter.empty:
            continue
        params[name] = _serializable_default(param.default)
    return params


def _default_params_by_name(params: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(params, dict):
        return {}
    return {
        str(name).lower(): copy.deepcopy(value)
        for name, value in params.items()
        if isinstance(value, dict)
    }


def _registry_catalog_items(
    registry: dict[str, dict[str, Any]],
    default_params: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    defaults = default_params or {}
    items: list[dict[str, Any]] = []
    for name, entry in sorted(registry.items()):
        cls = entry.get("cls")
        params = copy.deepcopy(defaults.get(str(name).lower(), {}))
        if not params and isinstance(cls, type):
            params = _constructor_defaults(cls)
        label = _split_identifier(getattr(cls, "__name__", "") or str(name))
        items.append(
            {
                "name": str(name).lower(),
                "label": label or _split_identifier(str(name)),
                "type": str(entry.get("type") or "custom"),
                "params": params,
            }
        )
    return items


def _technical_catalog_items(shared_params: dict[str, Any]) -> list[dict[str, Any]]:
    canonical_names = {
        "FairValueGap": "fvg",
        "LiquidityDetector": "liquidity",
        "MarketStructure": "market structure",
        "OrderBlockDetector": "obd",
    }
    seen: set[type] = set()
    items: list[dict[str, Any]] = []
    for fallback_name, entry in sorted(TechnicalRegistry._REGISTRY.items()):
        cls = entry.get("cls")
        if not isinstance(cls, type) or cls in seen:
            continue
        seen.add(cls)
        name = canonical_names.get(cls.__name__, str(fallback_name).lower())
        items.append(
            {
                "name": name,
                "label": _split_identifier(cls.__name__) or _split_identifier(name),
                "type": str(entry.get("type") or "structure"),
                "params": copy.deepcopy(shared_params),
            }
        )
    return items


def strategy_tool_catalog(app: FastAPI):
    load_builtin_indicators()
    defaults = _strategy_template()
    tools = defaults.get("tools", {}) if isinstance(defaults.get("tools"), dict) else {}
    indicators = tools.get("indicators", {}) if isinstance(tools.get("indicators"), dict) else {}
    technical = tools.get("technical", {}) if isinstance(tools.get("technical"), dict) else {}
    patterns = tools.get("patterns", {}) if isinstance(tools.get("patterns"), dict) else {}

    timeframes = ["5M", "15M", "30M", "1H", "2H", "4H", "6H", "8H", "1D"]
    configured_frames = defaults.get("timeframes", {})
    if isinstance(configured_frames, dict):
        for frame in configured_frames.values():
            frame_text = str(frame)
            if frame_text and frame_text not in timeframes:
                timeframes.append(frame_text)

    return {
        "defaults": defaults,
        "timeframes": timeframes,
        "indicators": _registry_catalog_items(
            IndicatorRegistry._REGISTRY,
            _default_params_by_name(indicators.get("params")),
        ),
        "technical": _technical_catalog_items(
            copy.deepcopy(technical.get("params") if isinstance(technical.get("params"), dict) else {})
        ),
        "patterns": _registry_catalog_items(
            PatternRegistry._REGISTRY,
            _default_params_by_name(patterns.get("params")),
        ),
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


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


def _read_csv_rows(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return rows[-limit:] if limit else rows


def _read_json_rows(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
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
    return rows[-limit:] if limit else rows


def _read_stats_history(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        rows = _read_csv_rows(path, limit)
        if not rows:
            return []
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
        rows = _read_json_rows(path, limit)
        if not rows:
            return []

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


def _row_profit(row: dict[str, Any]) -> float:
    for key in ("profit", "pnl", "net_profit", "realized_profit", "close_profit"):
        if row.get(key) not in (None, ""):
            return _safe_float(row.get(key))
    before = row.get("balance_before")
    after = row.get("balance_after")
    if before not in (None, "") and after not in (None, ""):
        return _safe_float(after) - _safe_float(before)
    return 0.0


def _row_is_trade(row: dict[str, Any]) -> bool:
    marker_keys = ("symbol", "ticket", "position_id", "order", "deal", "volume")
    if any(row.get(key) not in (None, "") for key in marker_keys):
        return True
    kind = str(row.get("type") or row.get("entry") or row.get("action") or "").lower()
    return any(token in kind for token in ("trade", "buy", "sell", "deal"))


def _row_cashflow(row: dict[str, Any]) -> dict[str, Any] | None:
    kind = str(row.get("type") or row.get("action") or row.get("reason") or row.get("comment") or "").lower()
    explicit = row.get("deposit") or row.get("withdrawal") or row.get("cashflow") or row.get("amount")
    is_cashflow = any(token in kind for token in ("deposit", "withdraw", "balance", "credit"))
    if explicit in (None, "") and not is_cashflow:
        return None

    amount = _safe_float(explicit) if explicit not in (None, "") else _row_profit(row)
    timestamp = str(row.get("timestamp") or row.get("time") or row.get("created_at") or "")
    return {
        "timestamp": timestamp,
        "date": (_parse_timestamp(timestamp) or datetime.now(timezone.utc)).date().isoformat(),
        "amount": amount,
        "type": "deposit" if amount >= 0 else "withdrawal",
        "note": str(row.get("comment") or row.get("reason") or row.get("type") or "Account movement"),
    }


def _mt5_deal_timestamp(row: dict[str, Any]) -> str:
    raw = row.get("time") or row.get("time_msc") or row.get("timestamp")
    if isinstance(raw, (int, float)):
        divisor = 1000 if raw > 10_000_000_000 else 1
        return datetime.fromtimestamp(raw / divisor, tz=timezone.utc).isoformat()
    return str(raw or "")


def _mt5_deal_kind(row: dict[str, Any]) -> str:
    text = " ".join(
        str(row.get(key) or "").lower()
        for key in ("comment", "type", "reason", "entry")
    )
    if "deposit" in text or "withdraw" in text or "balance" in text or "credit" in text:
        return "cashflow"
    if row.get("symbol") or row.get("volume") or row.get("position_id"):
        return "trade"
    return "trade" if _safe_float(row.get("profit")) else "cashflow"


def _mt5_deals_to_points(deals: list[dict[str, Any]], account: dict[str, Any]) -> list[dict[str, Any]]:
    if not deals:
        return []
    balance = _safe_float(account.get("balance") or account.get("equity"))
    sorted_deals = sorted(deals, key=lambda row: row.get("time_msc") or row.get("time") or 0)

    total_delta = 0.0
    deltas: list[tuple[str, float]] = []
    for deal in sorted_deals:
        profit = _safe_float(deal.get("profit"))
        commission = _safe_float(deal.get("commission"))
        swap = _safe_float(deal.get("swap"))
        fee = _safe_float(deal.get("fee"))
        delta = profit + commission + swap + fee
        if _mt5_deal_kind(deal) == "cashflow" and not delta:
            delta = _safe_float(deal.get("profit") or deal.get("amount"))
        timestamp = _mt5_deal_timestamp(deal)
        total_delta += delta
        deltas.append((timestamp, delta))

    running = balance - total_delta
    points: list[dict[str, Any]] = []
    for timestamp, delta in deltas:
        running += delta
        points.append({"timestamp": timestamp, "equity": running, "balance": running})
    return points


def _mt5_deals_to_activity_rows(deals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for deal in deals:
        profit = (
            _safe_float(deal.get("profit"))
            + _safe_float(deal.get("commission"))
            + _safe_float(deal.get("swap"))
            + _safe_float(deal.get("fee"))
        )
        timestamp = _mt5_deal_timestamp(deal)
        row = {
            "timestamp": timestamp,
            "profit": profit,
            "symbol": deal.get("symbol") or "",
            "ticket": deal.get("ticket") or deal.get("order") or "",
            "volume": deal.get("volume") or 0,
            "comment": deal.get("comment") or "",
            "type": _mt5_deal_kind(deal),
        }
        if row["type"] == "cashflow":
            row["amount"] = profit
        rows.append(row)
    return rows


def _read_mt5_account_history(ctx: DashboardContext, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    client = getattr(ctx, "client", None)
    if client is None:
        return [], []
    getter = getattr(client, "get_account_deals", None)
    if not callable(getter):
        return [], []
    try:
        deals = getter(datetime.now(timezone.utc) - timedelta(days=365), datetime.now(timezone.utc))
        if not deals:
            return [], []
        account = _safe_account_snapshot(ctx)
        points = _mt5_deals_to_points(deals, account)[-limit:]
        return points, _mt5_deals_to_activity_rows(deals)
    except Exception:
        logger.exception("Failed to seed account history from MT5 deals")
        return [], []


def _build_account_activity(rows: list[dict[str, Any]], points: list[dict[str, Any]]) -> dict[str, Any]:
    daily: dict[str, dict[str, Any]] = {}
    cashflows: list[dict[str, Any]] = []

    for row in rows:
        timestamp = row.get("timestamp") or row.get("time") or row.get("created_at")
        parsed = _parse_timestamp(timestamp)
        day = parsed.date().isoformat() if parsed else str(timestamp or "")[:10]
        if not day:
            continue

        movement = _row_cashflow(row)
        if movement is not None and not _row_is_trade(row):
            cashflows.append(movement)
            continue

        if not _row_is_trade(row):
            continue

        pnl = _row_profit(row)
        bucket = daily.setdefault(
            day,
            {"date": day, "profit": 0.0, "loss": 0.0, "net": 0.0, "trades": 0},
        )
        bucket["trades"] += 1
        bucket["net"] += pnl
        if pnl >= 0:
            bucket["profit"] += pnl
        else:
            bucket["loss"] += pnl

    if not daily and len(points) > 1:
        previous = points[0]
        for point in points[1:]:
            timestamp = point.get("timestamp")
            parsed = _parse_timestamp(timestamp)
            day = parsed.date().isoformat() if parsed else str(timestamp or "")[:10]
            if not day:
                previous = point
                continue
            delta = _safe_float(point.get("equity")) - _safe_float(previous.get("equity"))
            bucket = daily.setdefault(
                day,
                {"date": day, "profit": 0.0, "loss": 0.0, "net": 0.0, "trades": 0},
            )
            bucket["net"] += delta
            if delta >= 0:
                bucket["profit"] += delta
            else:
                bucket["loss"] += delta
            previous = point

    daily_rows = sorted(daily.values(), key=lambda item: item["date"])
    total_profit = sum(_safe_float(item.get("profit")) for item in daily_rows)
    total_loss = sum(_safe_float(item.get("loss")) for item in daily_rows)
    total_trades = sum(int(item.get("trades") or 0) for item in daily_rows)

    return {
        "daily": daily_rows[-60:],
        "cashflows": sorted(cashflows, key=lambda item: item.get("timestamp") or item.get("date"))[-50:],
        "summary": {
            "total_profit": total_profit,
            "total_loss": total_loss,
            "net": total_profit + total_loss,
            "trades": total_trades,
            "deposits": sum(_safe_float(item.get("amount")) for item in cashflows if _safe_float(item.get("amount")) > 0),
            "withdrawals": sum(_safe_float(item.get("amount")) for item in cashflows if _safe_float(item.get("amount")) < 0),
        },
    }


def _safe_account_snapshot(ctx: DashboardContext) -> dict[str, Any]:
    client = getattr(ctx, "client", None)
    if client is not None:
        getter = getattr(client, "get_account_snapshot", None)
        try:
            if callable(getter):
                snapshot = getter()
            else:
                snapshot = getattr(client, "account_info", None)
            if isinstance(snapshot, dict):
                return snapshot
        except Exception:
            logger.exception("Failed to read live account snapshot")

    config_path = _project_root() / "configs.json"
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        account = data.get("account_data")
        if isinstance(account, dict):
            return {
                "balance": _safe_float(account.get("deposit") or account.get("balance")),
                "equity": _safe_float(account.get("Equity") or account.get("equity")),
                "max_open_trades": account.get("max_open_trades"),
                "max_daily_loss": account.get("max_daily_loss"),
                "max_concurrent_trades": account.get("max_concurrent_trades"),
            }
    except Exception:
        logger.debug("No account_data fallback available", exc_info=True)
    return {}


def account_history(app: FastAPI, limit: int = 200):
    limit = max(10, min(limit, 2000))
    ctx = app.state.ctx
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

    activity_rows: list[dict[str, Any]] = []
    points = _read_stats_history(stats_path, limit)
    source = "trading_stats"
    if stats_path.exists():
        try:
            activity_rows = _read_csv_rows(stats_path, 2000)
        except Exception:
            logger.exception("Failed to read account activity stats rows")

    if not points:
        points = _read_trade_log_history(trade_log_path, limit)
        source = "trades_log" if points else source
        if trade_log_path.exists():
            try:
                activity_rows = _read_json_rows(trade_log_path, 2000)
            except Exception:
                logger.exception("Failed to read account activity trade rows")

    if not points:
        points, activity_rows = _read_mt5_account_history(ctx, limit)
        source = "mt5_terminal" if points else source

    if not points:
        points = _read_state_history(state_path)
        source = "state" if points else source

    return {
        "source": source,
        "account": _safe_account_snapshot(ctx),
        "points": points,
        "summary": _build_summary(points),
        "activity": _build_account_activity(activity_rows, points),
    }


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
    def _run_backtest(payload: BacktestTriggerPayload | None = None):
        return run_backtest(app, payload)

    @app.post("/strategy/create")
    def _create_strategy(payload: StrategyCreatePayload):
        return create_strategy(app, payload)

    @app.get("/strategy/catalog")
    def _strategy_catalog():
        return strategy_tool_catalog(app)

    @app.get("/strategy/registry")
    def _strategy_registry():
        return strategy_registry_snapshot(app)

    @app.get("/strategy/list")
    def _strategy_list():
        return strategy_registry_list(app)


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
