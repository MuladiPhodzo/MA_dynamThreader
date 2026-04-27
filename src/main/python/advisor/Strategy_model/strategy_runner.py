from __future__ import annotations

import asyncio
import copy
import inspect
import threading
from datetime import datetime, timezone
from typing import Any

from advisor.core.health_bus import HealthBus
from advisor.core.event_bus import EventBus
from advisor.core import events
from advisor.core.state import BotLifecycle, StateManager, Strategy, SymbolState
from advisor.Strategy_model.strategy import StrategyModel
from advisor.Strategy_model.strategy_registry import StrategyRegistry
from advisor.Strategy_model.signals.signal_store import SignalStore
from advisor.scheduler.process_sceduler import ProcessScheduler
from advisor.utils import dataHandler
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.utils.logging_setup import get_logger

logger = get_logger("Strategy_Manager")


class OrchestratedStrategy:
    """
    Lightweight fallback wrapper that materializes the default StrategyModel from cache.

    Kept for compatibility with tests and runtime fallback behavior.
    """

    def __init__(self, bundle: dict[str, Any]):
        self.bundle = bundle

    def run(self):
        symbol = self.bundle.get("symbol")
        cache = self.bundle.get("cache")
        if symbol is None or cache is None:
            return None

        live_mode = bool(self.bundle.get("live_mode", False))
        config = self.bundle.get("config")

        try:
            strategy = StrategyModel(symbol, cache, config=config)
            if live_mode and not getattr(strategy, "live_mode", False):
                strategy.enable_live_mode()
            return strategy.run()
        except Exception:
            logger.exception("Orchestrated strategy failed for %s", symbol)
            return None


class StrategyError(Exception):
    pass


class StrategyManager:
    """
    Live strategy orchestration manager.

    Responsibilities:
    - Subscribe to market data events.
    - Create/attach StrategyModel instances to SymbolState objects.
    - Emit RUN_BACKTEST:<strategy_name> when a new strategy is created.
    - Run enabled symbol strategies on market data.
    - Normalize strategy outputs into trade signals.
    - Store and publish SIGNAL_GENERATED events.

    Non-responsibilities:
    - No backtest execution.
    - No backtest scheduling.
    - No backtest result persistence.

    Backtesting belongs to:
        advisor.backtest.backtest_runner.BacktestProcess
        advisor.backtest.core.Backtest
    """

    name = "strategy"

    def __init__(
        self,
        scheduler: ProcessScheduler,
        event_bus: EventBus,
        shutdown_event: threading.Event,
        heartbeats: dict,
        health_bus: HealthBus,
        cache_handler: dataHandler.CacheManager,
        symbol_watch: SymbolWatch,
        store: SignalStore,
        state_manager: StateManager,
        trade_config: dict | None = None,
        emit_backtest_on_create: bool = True,
    ):
        self.scheduler = scheduler
        self.event_bus = event_bus
        self.stop_event = shutdown_event
        self.heartbeats = heartbeats
        self.health_bus = health_bus
        self.cache = cache_handler
        self.symbol_watch = symbol_watch
        self.signal_store = store
        self.state = state_manager
        self.trade_config = dict(trade_config or {})
        self.emit_backtest_on_create = bool(emit_backtest_on_create)

        self.default_sl_points = self._resolve_default_sl_points()
        self.default_tp_points = self._resolve_default_tp_points()

        self._running: set[str] = set()
        self._subscribed_symbols: set[str] = set()
        self._logged_cache_ready: set[str] = set()
        self._logged_cache_empty: set[str] = set()
        self._emitted_backtests: set[str] = set()

        self.strategy_registry = StrategyRegistry()
        self.strategy_registry.refresh_configs(persist=False)

    # -------------------------------------------------
    # Registration
    # -------------------------------------------------

    def register(self) -> None:
        """
        Subscribe only to live strategy orchestration events.

        Backtest events are handled by BacktestProcess, not StrategyManager.
        """
        self.event_bus.subscribe(events.MARKET_DATA_READY, self._on_symbols)
        self.event_bus.subscribe(events.STRATEGY_CONFIG_UPDATED, self._on_strategy_catalog_update)
        self.event_bus.subscribe(events.CREATE_STRATEGY, self._on_create_strategy)

        for symbol in self.symbol_watch.all_symbol_names():
            self._subscribe_symbol(symbol)

        self._mark_service_health("registered")

    def _on_strategy_catalog_update(self, event) -> None:
        del event
        self.strategy_registry.refresh_configs(persist=False)

    def _configured_strategy(self, strategy_name: str | None) -> dict[str, Any] | None:
        return self.strategy_registry.get_config(strategy_name)

    def _on_create_strategy(self, event) -> None:
        if event and getattr(event, "payload", None):
            payload = event.payload
            strategy_name = payload.get("strategy_name")
            config = payload.get("config")
            if not isinstance(config, dict):
                config = {}
            if strategy_name:
                config = {**config, "name": strategy_name}
            if config:
                self.create_strategy(
                    strategy=None,
                    config=config,
                    source="runtime_create",
                    emit_backtest=True,
                )

    def _subscribe_symbol(self, symbol: str) -> None:
        if symbol in self._subscribed_symbols:
            return

        self._subscribed_symbols.add(symbol)
        self.event_bus.subscribe(
            f"{events.MARKET_DATA_READY}:{symbol}",
            lambda evt, s=symbol: self._spawn_market_data_task(s, evt),
        )

    def _on_symbols(self, event) -> None:
        symbols: list[str] = []
        if event and getattr(event, "payload", None):
            payload_symbols = event.payload.get("symbols")
            if isinstance(payload_symbols, list):
                symbols = payload_symbols

        if not symbols:
            symbols = self.symbol_watch.all_symbol_names()

        for symbol in symbols:
            self._subscribe_symbol(symbol)

    # -------------------------------------------------
    # Strategy creation / attachment
    # -------------------------------------------------
    def create_strategy(
        self,
        strategy: StrategyModel | None = None,
        *,
        config: dict[str, Any] | None = None,
        source: str = "runtime_attach",
        emit_backtest: bool | None = None,
    ) -> dict[str, Any] | None:
        """
        Register a new strategy config and request top-20 backtest.

        Does NOT instantiate or attach the strategy.
        BacktestProcess/Core will create StrategyModel instances during backtest.

        Emits:
            RUN_BACKTEST:<strategy_name>
        """
        try:
            strategy_config = self._strategy_config(strategy, config)
            strategy_name = self._strategy_name(strategy, strategy_config)

            if not strategy_name:
                raise StrategyError("strategy name is required")

            strategy_config["name"] = strategy_name

            _, stored = self.strategy_registry.upsert_config(
                strategy_name,
                strategy_config,
                overwrite=True,
            )
            self.strategy_registry.refresh_configs(persist=False)

            should_emit = self.emit_backtest_on_create if emit_backtest is None else bool(emit_backtest)
            if should_emit:
                self._queue_backtest_request(strategy_name)

            self._mark_service_health("strategy_registered")

            logger.info(
                "Registered strategy %s from %s and requested top-20 backtest",
                strategy_name,
                source,
            )

            return {
                "strategy_name": strategy_name,
                "config": stored if isinstance(stored, dict) else strategy_config,
                "backtest_requested": should_emit,
            }

        except Exception as exc:
            logger.exception("Failed to register strategy: %s", exc)
            self.health_bus.update(
                self.name,
                "ERROR",
                {
                    "phase": "strategy_register_failed",
                    "error": str(exc),
                },
            )
            return None

    def create_symbol_strategy(
        self,
        symbol: str,
        strategy: StrategyModel | None = None,
        *,
        config: dict[str, Any] | None = None,
        source: str = "runtime_attach",
        emit_backtest: bool | None = None,
    ) -> Strategy | None:
        """
        Create and attach a StrategyModel to a symbol.

        Emits:
            RUN_BACKTEST:<strategy_name>

        when a new strategy is attached, unless disabled.
        """
        sym = self.symbol_watch.get(symbol)
        if sym is None:
            logger.warning("Cannot create strategy for unknown symbol %s", symbol)
            return None

        if not isinstance(getattr(sym, "strategies", None), list):
            sym.strategies = list(getattr(sym, "strategies", None) or [])

        if strategy is None:
            strategy = StrategyModel(symbol, self.cache, config=config)
            if self._should_use_live_mode() and not getattr(strategy, "live_mode", False):
                strategy.enable_live_mode()

        strategy_name = getattr(strategy, "strategy_name", None) or type(strategy).__name__
        strategy_key = self._strategy_key(strategy_name)

        for existing in sym.strategies:
            if self._strategy_key(getattr(existing, "strategy_name", None)) == strategy_key:
                return existing

        wrapper = Strategy(strategy_name, strategy)
        sym.strategies.append(wrapper)

        self.strategy_registry.record_attach(
            symbol=symbol,
            strategy_name=strategy_name,
            config=getattr(strategy, "config", None),
            source=source,
        )

        should_emit = self.emit_backtest_on_create if emit_backtest is None else bool(emit_backtest)
        if should_emit:
            self._queue_backtest_request(strategy_name)

        logger.info("Attached strategy %s to %s", strategy_name, symbol)
        return wrapper

    def _strategy_config(
        self,
        strategy: StrategyModel | None,
        config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        base = copy.deepcopy(StrategyModel.DEFAULT_CONFIG)
        strategy_config = getattr(strategy, "config", None)
        if isinstance(strategy_config, dict):
            base = self._deep_merge_dict(base, strategy_config)
        if isinstance(config, dict):
            base = self._deep_merge_dict(base, config)
        return base

    def _strategy_name(
        self,
        strategy: StrategyModel | None,
        config: dict[str, Any],
    ) -> str:
        candidates = (
            config.get("name") if isinstance(config, dict) else None,
            getattr(strategy, "strategy_name", None),
            getattr(strategy, "name", None),
            type(strategy).__name__ if strategy is not None else None,
            StrategyModel.DEFAULT_CONFIG.get("name"),
        )
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text:
                return text
        return ""

    def _queue_backtest_request(self, strategy_name: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            emitter = getattr(self.event_bus, "emit", None)
            if callable(emitter):
                self._emit_backtest_request_sync(strategy_name)
                return
            asyncio.run(self._emit_backtest_request(strategy_name))
            return
        loop.create_task(self._emit_backtest_request(strategy_name))

    def _emit_backtest_request_sync(self, strategy_name: str) -> None:
        if not strategy_name:
            return

        key = self._strategy_key(strategy_name)
        if key in self._emitted_backtests:
            return

        self._emitted_backtests.add(key)
        event_name, payload = self._backtest_request_event(strategy_name)

        try:
            self.event_bus.emit(event_name, payload)
            logger.info("Emitted %s after strategy creation", event_name)
        except Exception:
            logger.exception("Failed to emit backtest request for %s", strategy_name)

    async def _emit_backtest_request(self, strategy_name: str) -> None:
        """
        Emit strategy-level backtest request once per strategy name per runtime.

        Event shape:
            RUN_BACKTEST:<strategy_name>
        """
        if not strategy_name:
            return

        key = self._strategy_key(strategy_name)
        if key in self._emitted_backtests:
            return

        self._emitted_backtests.add(key)
        event_name, payload = self._backtest_request_event(strategy_name)

        try:
            await self.event_bus.publish(event_name, payload)
            logger.info("Emitted %s after strategy creation", event_name)
        except Exception:
            logger.exception("Failed to emit backtest request for %s", strategy_name)

    def _backtest_request_event(self, strategy_name: str) -> tuple[str, dict[str, Any]]:
        event_name = f"{events.RUN_BACKTEST}:{strategy_name}"
        return event_name, {
            "type": event_name,
            "strategy_name": strategy_name,
            "source": self.name,
            "top_n": 20,
            "timestamp": datetime.now(timezone.utc),
        }

    def attach_configured_strategy(self, symbol: str, strategy_name: str) -> Strategy | None:
        """
        Attach a strategy from StrategyRegistry configuration.
        """
        config = self._configured_strategy(strategy_name)
        if config is None:
            logger.warning("Configured strategy not found: %s", strategy_name)
            return None

        try:
            strategy = StrategyModel(symbol, self.cache, config=dict(config))
            if self._should_use_live_mode() and not getattr(strategy, "live_mode", False):
                strategy.enable_live_mode()
            return self.create_symbol_strategy(
                symbol=symbol,
                strategy=strategy,
                config=config,
                source="configured_strategy",
            )
        except Exception:
            logger.exception("Failed to create configured strategy %s for %s", strategy_name, symbol)
            self.strategy_registry.record_error(
                symbol=symbol,
                strategy_name=strategy_name,
                error="configured strategy init failed",
                phase="config_attach",
            )
            return None

    # -------------------------------------------------
    # Market data orchestration
    # -------------------------------------------------

    def _spawn_market_data_task(self, symbol: str, event) -> None:
        task = asyncio.create_task(self._on_market_data(symbol, event))
        task.add_done_callback(lambda done, s=symbol: self._on_market_data_done(s, done))

    def _on_market_data_done(self, symbol: str, task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except BaseException:
            if self.stop_event.is_set():
                return
            logger.exception("Market data task failed for %s", symbol)

    async def _on_market_data(self, symbol: str, event) -> None:
        del event
        if self.stop_event.is_set():
            return

        if symbol in self._running:
            return

        sym_state = self.symbol_watch.get(symbol)
        if sym_state is None:
            return

        if not getattr(sym_state, "strategies", []):
            try:
                self.create_symbol_strategy(symbol)
            except Exception as exc:
                logger.exception("Failed to create strategy for %s: %s", symbol, exc)
                self.symbol_watch.mark_error(symbol, f"strategy init failed: {exc}")
                self.strategy_registry.record_error(
                    symbol=symbol,
                    strategy_name=StrategyModel.DEFAULT_CONFIG.get("name", "strategy"),
                    error=str(exc),
                    phase="init",
                )
                return

        self._running.add(symbol)

        try:
            self.state.set_state(BotLifecycle.RUNNING)
            self._mark_service_health("running", symbol=symbol)

            await self.scheduler.schedule(
                process_name=f"{self.name}:{symbol}",
                required_resources=[],
                task=lambda: self._run_symbol(symbol),
                shutdown_event=self.stop_event,
                heartbeats=self.heartbeats,
                timeout=120,
            )

        except Exception as exc:
            self.state.set_state(BotLifecycle.DEGRADED)
            self.health_bus.update(f"{self.name}:{symbol}", "ERROR", {"error": str(exc)})
            logger.exception("Failed to schedule strategy for %s: %s", symbol, exc)
            self.symbol_watch.mark_error(symbol, f"scheduling failed: {exc}")
            self.strategy_registry.record_error(
                symbol=symbol,
                strategy_name=StrategyModel.DEFAULT_CONFIG.get("name", "strategy"),
                error=f"strategy scheduling failed: {exc}",
                phase="live_schedule",
            )

        finally:
            self._running.discard(symbol)
            self._mark_service_health("idle", symbol=symbol)

    async def _run_symbol(self, symbol: str) -> None:
        state = self.symbol_watch.get(symbol)
        if state is None or not getattr(state, "enabled", False):
            return

        if not self._symbol_ready(state):
            self._log_warmup(symbol)
            return

        if not isinstance(getattr(state, "strategies", None), list):
            state.strategies = list(getattr(state, "strategies", None) or [])

        produced = 0

        if state.strategies:
            for strat in state.strategies:
                strategy_name = getattr(strat, "strategy_name", type(strat).__name__)
                try:
                    payload = await asyncio.to_thread(self._build_signal, symbol, strat)
                    if payload is None:
                        continue

                    self.strategy_registry.record_signal(symbol, strategy_name, payload)
                    await self._publish_signal(symbol, payload)
                    produced += 1

                except Exception as exc:
                    logger.exception("Strategy failed for %s: %s", symbol, exc)
                    self.symbol_watch.mark_error(symbol, str(exc))
                    self.strategy_registry.record_error(
                        symbol=symbol,
                        strategy_name=strategy_name,
                        error=str(exc),
                        phase="live",
                    )
        else:
            try:
                payload = await asyncio.to_thread(self._build_orchestrated_signal, symbol)
                if payload is not None:
                    strategy_name = str(
                        payload.get("data", {}).get("strategy_name")
                        if isinstance(payload.get("data"), dict)
                        else "OrchestratedStrategy"
                    )
                    self.strategy_registry.record_signal(symbol, strategy_name, payload)
                    await self._publish_signal(symbol, payload)
                    produced += 1
            except Exception as exc:
                logger.exception("Orchestrated strategy failed for %s: %s", symbol, exc)
                self.symbol_watch.mark_error(symbol, str(exc))
                self.strategy_registry.record_error(
                    symbol=symbol,
                    strategy_name="OrchestratedStrategy",
                    error=str(exc),
                    phase="live_orchestrated",
                )

        self.heartbeats[f"{self.name}:{symbol}"] = datetime.now(timezone.utc).isoformat()
        self.health_bus.update(
            f"{self.name}:{symbol}",
            "RUNNING",
            {
                "signals": produced,
                "symbol": symbol,
                "strategies": len(state.strategies),
            },
        )

    # -------------------------------------------------
    # Strategy execution / signal normalization
    # -------------------------------------------------

    def _build_signal(self, symbol: str, strat) -> dict | None:
        strategy_callable = getattr(strat, "strategy", strat)
        if not callable(strategy_callable):
            return None

        strategy_callable = self._prepare_strategy_callable(strategy_callable)
        if strategy_callable is None:
            return None

        result = self._invoke_strategy(strategy_callable)
        return self._normalize_signal(symbol, result, strat)

    def _build_orchestrated_signal(self, symbol: str) -> dict | None:
        data = self.cache.get(symbol)
        if not data:
            return None

        try:
            strategy = OrchestratedStrategy(
                {
                    "symbol": symbol,
                    "cache": self.cache,
                    "data": data,
                    "config": getattr(self, "strategy_config", None),
                    "live_mode": self._should_use_live_mode(),
                }
            )
            result = strategy.run()
            return self._normalize_signal(symbol, result, strategy)
        except Exception:
            logger.exception("Failed to build orchestrated signal for %s", symbol)
            return None

    def _prepare_strategy_callable(self, strategy_callable):
        if self._should_use_live_mode() and hasattr(strategy_callable, "enable_live_mode"):
            if not getattr(strategy_callable, "live_mode", False):
                strategy_callable.enable_live_mode()
        return strategy_callable

    def _invoke_strategy(self, strategy_callable):
        try:
            result = strategy_callable()
        except TypeError as exc:
            if not self._looks_like_missing_argument(exc):
                raise
            result = strategy_callable(False)

        if inspect.isawaitable(result):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(result)
            raise RuntimeError("Async strategies must be executed via the scheduler")

        return result

    def _normalize_signal(self, symbol: str, raw: Any, strat=None) -> dict | None:
        if not isinstance(raw, dict):
            return None

        if self._is_weak_signal(raw):
            return None

        side = self._extract_side(raw)
        if not side:
            return None

        confidence = self._safe_float(
            raw.get("confidence", raw.get("score", 50.0)),
            default=50.0,
        )

        sl_points, tp_points = self._resolve_stop_targets(raw)

        metadata = dict(raw.get("metadata", {})) if isinstance(raw.get("metadata"), dict) else {}
        metadata.setdefault(
            "strategy_name",
            getattr(strat, "strategy_name", type(strat).__name__ if strat is not None else None),
        )
        metadata.setdefault("direction", side.title())
        metadata["score"] = self._coerce_float(
            raw.get("score", metadata.get("score")),
            default=confidence / 100.0,
        )
        metadata.setdefault("confidence", confidence)
        metadata.setdefault("sig", raw.get("sig"))
        metadata.setdefault("sl_distance", raw.get("sl", metadata.get("sl_distance")))
        metadata.setdefault("tp_distance", raw.get("tp", metadata.get("tp_distance")))
        metadata.setdefault("source", "strategy")

        payload = {
            "id": raw.get("id") or f"{symbol}:{datetime.now(timezone.utc).isoformat()}",
            "symbol": symbol,
            "side": side,
            "sl": sl_points,
            "tp": tp_points,
            "confidence": confidence,
            "data": metadata,
            "timestamp": raw.get("timestamp") or datetime.now(timezone.utc),
        }

        if "frame" in raw:
            payload["frame"] = raw["frame"]
        if "direction" in raw:
            payload["direction"] = raw["direction"]
        if "sig" in raw:
            payload["sig"] = raw["sig"]

        return payload

    # -------------------------------------------------
    # Readiness / health
    # -------------------------------------------------

    def _symbol_ready(self, symbol: SymbolState) -> bool:
        cached = self.cache.get(symbol.symbol)
        if cached:
            self._log_cache_ready(symbol.symbol, cached)
            return True

        self._log_cache_empty(symbol.symbol)
        telem = self.symbol_watch.get_telemetry(symbol.symbol)
        return bool(telem and telem.data_fetch_count > 0)

    def _mark_service_health(self, phase: str, symbol: str | None = None) -> None:
        stamp = datetime.now(timezone.utc).isoformat()
        self.heartbeats[self.name] = stamp
        payload = {
            "phase": phase,
            "running": len(self._running),
            "subscribed_symbols": len(self._subscribed_symbols),
        }
        if symbol:
            payload["symbol"] = symbol
        self.health_bus.update(self.name, "RUNNING", payload)

    def _log_cache_ready(self, symbol: str, data) -> None:
        if symbol in self._logged_cache_ready:
            return
        self._logged_cache_ready.add(symbol)
        logger.info("Strategy data ready for %s: %s", symbol, self._summarize_cache(data))

    def _log_cache_empty(self, symbol: str) -> None:
        if symbol in self._logged_cache_empty:
            return
        self._logged_cache_empty.add(symbol)
        logger.info("Strategy data missing for %s (cache empty at init)", symbol)

    @staticmethod
    def _summarize_cache(data) -> str:
        if isinstance(data, dict):
            parts = []
            for tf, df in data.items():
                try:
                    rows = len(df) if hasattr(df, "__len__") else None
                except Exception:
                    rows = None
                parts.append(str(tf) if rows is None else f"{tf}:{rows}")
            return "tfs=" + ",".join(parts) if parts else "tfs=none"
        return f"type={type(data).__name__}"

    @staticmethod
    def _log_warmup(symbol: str) -> None:
        logger.debug("%s: waiting for warm-up data", symbol)

    # -------------------------------------------------
    # Signal helpers
    # -------------------------------------------------

    @staticmethod
    def _is_weak_signal(raw: dict) -> bool:
        parts = [str(raw.get(key, "")).lower() for key in ("sig", "direction", "side")]
        text = " ".join(parts)
        return "(w)" in text or "weak" in text

    def _extract_side(self, raw: dict) -> str | None:
        for candidate in (raw.get("side"), raw.get("direction"), raw.get("sig")):
            side = self._normalize_side(candidate)
            if side:
                return side
        return None

    @staticmethod
    def _normalize_side(value: Any) -> str | None:
        text = str(value or "").strip().lower()
        if not text:
            return None
        if text in {"buy", "bullish", "long", "up"}:
            return "buy"
        if text in {"sell", "bearish", "short", "down"}:
            return "sell"
        if "bullish" in text:
            return "buy"
        if "bearish" in text:
            return "sell"
        return None

    def _resolve_stop_targets(self, raw: dict) -> tuple[float, float]:
        metadata = raw.get("metadata", {}) if isinstance(raw.get("metadata"), dict) else {}

        sl_points = self._coerce_float(
            raw.get("sl", metadata.get("sl_distance", metadata.get("sl"))),
            default=0.0,
        )
        tp_points = self._coerce_float(
            raw.get("tp", metadata.get("tp_distance", metadata.get("tp"))),
            default=0.0,
        )

        frame = raw.get("frame")
        if (sl_points <= 0.0 or tp_points <= 0.0) and hasattr(frame, "iloc"):
            try:
                latest = frame.iloc[-1]
                if sl_points <= 0.0:
                    sl_points = self._coerce_float(
                        latest.get("SL", latest.get("sl", latest.get("stop_loss", 0.0))),
                        default=0.0,
                    )
                if tp_points <= 0.0:
                    tp_points = self._coerce_float(
                        latest.get("TP", latest.get("tp", latest.get("take_profit", 0.0))),
                        default=0.0,
                    )
            except Exception:
                logger.debug("Failed to resolve SL/TP from strategy frame", exc_info=True)

        if sl_points <= 0.0:
            sl_points = float(self.default_sl_points)
        if tp_points <= 0.0:
            tp_points = float(self.default_tp_points)

        return round(float(sl_points), 2), round(float(tp_points), 2)

    async def _publish_signal(self, symbol: str, payload: dict) -> None:
        payload = dict(payload or {})
        payload.setdefault("symbol", symbol)
        payload.setdefault("timestamp", datetime.now(timezone.utc))

        self.signal_store.add_signal(payload)
        self.symbol_watch.mark_signal(symbol)

        await self.event_bus.publish(events.SIGNAL_GENERATED, payload)
        await self.event_bus.publish(f"{events.SIGNAL_GENERATED}:{symbol}", payload)

    # -------------------------------------------------
    # Config helpers
    # -------------------------------------------------

    def _should_use_live_mode(self) -> bool:
        bot = getattr(self.state, "bot", None)
        if bot is None:
            return False
        return bool(getattr(bot, "live_trading_enabled", True))

    def _resolve_default_sl_points(self) -> int:
        raw = self.trade_config.get("pip_distance", self.trade_config.get("sl_distance", 200))
        try:
            value = int(float(raw))
        except Exception:
            value = 200
        return max(1, value)

    def _resolve_default_tp_points(self) -> int:
        raw = self.trade_config.get("tp_distance")
        if raw is not None:
            try:
                value = int(float(raw))
                return max(1, value)
            except Exception:
                pass

        rr_ratio = self._parse_rr_ratio(self.trade_config.get("rr_ratio", "1:2"))
        return max(1, int(round(self.default_sl_points * rr_ratio)))

    @staticmethod
    def _parse_rr_ratio(value: Any) -> float:
        if isinstance(value, (int, float)):
            return max(float(value), 1.0)

        text = str(value or "").strip()
        if ":" in text:
            left, _, right = text.partition(":")
            try:
                denom = float(left)
                numer = float(right)
                if denom <= 0:
                    return 2.0
                return max(numer / denom, 1.0)
            except Exception:
                return 2.0

        try:
            return max(float(text), 1.0)
        except Exception:
            return 2.0

    # -------------------------------------------------
    # Generic helpers
    # -------------------------------------------------

    @staticmethod
    def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = copy.deepcopy(base)
        for key, value in (override or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = StrategyManager._deep_merge_dict(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged

    @staticmethod
    def _looks_like_missing_argument(exc: TypeError) -> bool:
        text = str(exc).lower()
        return (
            ("missing" in text and "argument" in text)
            or "positional argument" in text
            or "positional arguments" in text
            or "takes 0 positional" in text
        )

    @staticmethod
    def _coerce_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            if isinstance(value, str) and not value.strip():
                return default
            return float(value)
        except Exception:
            return default

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        return self._coerce_float(value, default)

    @staticmethod
    def _strategy_key(value: Any) -> str:
        text = str(value or "").strip().casefold()
        return "".join(ch for ch in text if ch.isalnum())
