from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from advisor.Client.mt5Client import MetaTrader5Client
from advisor.core.health_bus import HealthBus
from advisor.core.event_bus import EventBus
from advisor.core import events
from advisor.core.state import BotLifecycle, StateManager
from advisor.scheduler.requirements import ProcessRequirement
from advisor.utils.dataHandler import CacheManager
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.backtest.core import Backtest, BacktestBatchResult, BacktestResult
from advisor.utils.logging_setup import get_logger
from advisor.scheduler.process_sceduler import ProcessScheduler
from advisor.scheduler.resource_registry import ResourceRegistry

logger = get_logger("Backtest")

STATE_FILE = Path("bot_state.json")

BACKTEST_REQS = [
    ProcessRequirement("market_data", max_age=timedelta(minutes=10)),
    ProcessRequirement("symbol_ingestion", max_age=timedelta(minutes=10)),
]


class BacktestProcess:
    """
    Backtest event orchestrator.

    Supported events:
        RUN_BACKTEST:<strategy_name>:<symbol>
            Backtest a specific strategy on one symbol.

        RUN_BACKTEST:<strategy_name>
            Backtest one strategy on the top 20 symbols.
            Successful strategies are then seeded to the rest of the symbols.
    """

    name = "backtest"
    RUN_BACKTEST_PREFIX = events.RUN_BACKTEST

    def __init__(
        self,
        client: MetaTrader5Client,
        cache_handler: CacheManager,
        scheduler: ProcessScheduler,
        health_bus: HealthBus,
        heartbeats: dict,
        shutdown_event,
        state_manager: StateManager,
        symbol_watch: SymbolWatch,
        event_bus: EventBus,
        registry: ResourceRegistry,
        per_symbol_timeout: float = 120.0,
        max_concurrent: int = 4,
        run_on_first_start: bool = False,
        run_if_no_enabled: bool = False,
        default_strategy_name: str = "EMA_Proxim8te",
        pipeline: Any | None = None,
    ) -> None:
        self.client = client
        self.cache = cache_handler
        self.symbol_watch = symbol_watch
        self.scheduler = scheduler
        self.registry = registry
        self.registry.register(self.name)

        self.health_bus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event
        self.state_manager = state_manager
        self.event_bus = event_bus

        self.per_symbol_timeout = per_symbol_timeout
        self.max_concurrent = max_concurrent
        self.run_on_first_start = run_on_first_start
        self.run_if_no_enabled = run_if_no_enabled
        self.default_strategy_name = default_strategy_name

        try:
            self.backtest = Backtest(
                client=self.client,
                cache=self.cache,
                symbol_watch=self.symbol_watch,
                pipeline=pipeline,
            )
        except TypeError as exc:
            if "unexpected keyword" not in str(exc):
                raise
            self.backtest = Backtest(self.client, self.cache, self.symbol_watch)

        self._running_jobs: set[str] = set()
        self._subscribed = False
        self._no_enabled_forced = False

    # -------------------------------------------------
    # Registration
    # -------------------------------------------------

    def register(self) -> None:
        """
        Subscribe to backtest events only.

        This intentionally replaces the old MARKET_DATA_READY-driven behavior.
        """
        if self._subscribed:
            return

        self._subscribed = True

        self.event_bus.subscribe(
            events.RUN_BACKTEST,
            lambda evt: asyncio.create_task(self._trigger_from_event(evt)),
        )

        self.event_bus.subscribe(
            f"{events.RUN_BACKTEST}:*",
            lambda evt: asyncio.create_task(self._trigger_from_event(evt)),
        )

        logger.info("BacktestProcess registered for RUN_BACKTEST events")

        if self.run_on_first_start or self.run_if_no_enabled:
            asyncio.create_task(self._maybe_bootstrap_backtest())

    async def _maybe_bootstrap_backtest(self) -> None:
        await asyncio.sleep(0)
        if self.stop_event.is_set():
            return
        if not self._should_run_bootstrap():
            return

        await self._trigger(
            strategy_name=self.default_strategy_name,
            symbol=None,
            payload={"top_n": 20, "years": 5, "simulate": True},
        )

    # -------------------------------------------------
    # Event parsing / trigger
    # -------------------------------------------------

    async def _trigger_from_event(self, event) -> None:
        if self.stop_event.is_set():
            return

        parsed = self._parse_event(event)
        if parsed is None:
            logger.warning("Ignoring malformed backtest event: %r", event)
            return

        strategy_name, symbol, payload = parsed
        await self._trigger(strategy_name=strategy_name, symbol=symbol, payload=payload)

    def _parse_event(self, event) -> tuple[str, str | None, dict[str, Any]] | None:
        """
        Accepts:
            event.name = RUN_BACKTEST:strategy:symbol
            event.type/name = RUN_BACKTEST with payload
            raw str = RUN_BACKTEST:strategy:symbol
            raw dict = {"type": "RUN_BACKTEST", "strategy_name": "...", "symbol": "..."}
        """
        payload = getattr(event, "payload", None) or {}
        raw_name = (
            getattr(event, "name", None)
            or getattr(event, "type", None)
            or getattr(event, "event", None)
            or payload.get("event")
            or payload.get("type")
        )

        if isinstance(event, str):
            raw_name = event
            payload = {}

        if isinstance(event, dict):
            payload = event
            raw_name = event.get("event") or event.get("type") or event.get("name")

        if not raw_name:
            return None

        raw_name = str(raw_name)
        parts = raw_name.split(":")

        if parts[0] not in {self.RUN_BACKTEST_PREFIX, "RUN_BACKTEST"}:
            return None

        strategy_name = payload.get("strategy_name") or payload.get("strategy")
        symbol = payload.get("symbol")

        if len(parts) >= 2 and parts[1]:
            strategy_name = parts[1]
        if len(parts) >= 3 and parts[2]:
            symbol = parts[2]

        strategy_name = str(strategy_name or self.default_strategy_name)
        symbol = str(symbol).upper() if symbol else None

        return strategy_name, symbol, dict(payload)

    async def _trigger(self, strategy_name: str, symbol: str | None, payload: dict[str, Any]) -> None:
        job_key = f"{strategy_name}:{symbol or 'TOP20'}"

        if job_key in self._running_jobs:
            logger.warning("Backtest already running for %s", job_key)
            return

        self._running_jobs.add(job_key)

        try:
            async def _task():
                if symbol:
                    return await self._run_single(strategy_name, symbol, payload)
                return await self._run_top20(strategy_name, payload)

            await self.scheduler.schedule(
                process_name=f"{self.name}:{job_key}",
                required_resources=BACKTEST_REQS,
                task=_task,
                shutdown_event=self.stop_event,
                heartbeats=self.heartbeats,
                timeout=self._timeout_for(payload, single_symbol=bool(symbol)),
            )

        except SystemExit:
            return
        except Exception:
            logger.exception("Backtest trigger failed for %s", job_key)
        finally:
            self._running_jobs.discard(job_key)

    # -------------------------------------------------
    # Core execution
    # -------------------------------------------------

    async def _run_single(self, strategy_name: str, symbol: str, payload: dict[str, Any]) -> BacktestResult:
        self._set_running_state(True)

        try:
            self._heartbeat(strategy_name, symbol, "RUNNING", {"scope": "single"})

            result = await self.backtest.run_symbol(
                strategy_name=strategy_name,
                symbol=symbol,
                years=int(payload.get("years", 1) or 1),
                simulate=bool(payload.get("simulate", True)),
                spread_cost=float(payload.get("spread_cost", 0.0) or 0.0),
                max_bars_in_trade=int(payload.get("max_bars_in_trade", 100) or 100),
            )

            self._update_state_from_result(result)
            await self._publish_single_completed(result)

            return result

        finally:
            self._set_running_state(False)

    async def _run_top20(self, strategy_name: str, payload: dict[str, Any]) -> BacktestBatchResult:
        self._set_running_state(True)

        try:
            top_n = int(payload.get("top_n", 20) or 20)
            years = int(payload.get("years", 1) or 1)
            simulate = bool(payload.get("simulate", True))

            self._heartbeat(strategy_name, None, "RUNNING", {"scope": "top20", "top_n": top_n})

            summary = await self.backtest.run_top_symbols(
                strategy_name=strategy_name,
                top_n=top_n,
                years=years,
                simulate=simulate,
            )

            successful = [result.symbol for result in summary.results if result.passed]
            seeded = self.backtest.seed_successful_strategy_to_rest(strategy_name, successful)
            summary.seeded_symbols = seeded

            self._update_state_from_summary(summary)
            await self._publish_batch_completed(summary)

            return summary

        finally:
            self._set_running_state(False)

    # -------------------------------------------------
    # State / publishing
    # -------------------------------------------------

    def _set_running_state(self, running: bool) -> None:
        if running:
            self.state_manager.set_state(BotLifecycle.RUNNING_BACKTEST)
            self.state_manager.bot.backtest_running = True
        else:
            self.state_manager.bot.backtest_running = False
            self.state_manager.set_state(BotLifecycle.RUNNING)

        StateManager.save_bot_state(self.state_manager.bot)

    def _update_state_from_result(self, result: BacktestResult) -> None:
        timestamp = datetime.now(timezone.utc)
        self.state_manager.last_backtest_run = timestamp

        sym = self.symbol_watch.get(result.symbol)
        if sym is not None:
            sym.last_backtest = timestamp
            if not isinstance(sym.meta, dict):
                sym.meta = {}
            sym.meta["last_backtest_at"] = timestamp.isoformat()
            sym.meta["last_backtest_strategy"] = result.strategy_name
            sym.meta["last_backtest_score"] = result.score
            sym.meta["last_backtest_passed"] = result.passed

        StateManager.save_bot_state(self.state_manager.bot)

    def _update_state_from_summary(self, summary: BacktestBatchResult) -> None:
        timestamp = datetime.now(timezone.utc)
        self.state_manager.last_backtest_run = timestamp

        for result in summary.results:
            self._update_state_from_result(result)

        StateManager.save_bot_state(self.state_manager.bot)
        self._apply_backtest_scores()

    async def _publish_single_completed(self, result: BacktestResult) -> None:
        event_name = f"{events.BACKTEST_COMPLETED}:{result.strategy_name}:{result.symbol}"
        await self.event_bus.publish(
            event_name,
            {
                "strategy_name": result.strategy_name,
                "symbol": result.symbol,
                "ok": result.ok,
                "passed": result.passed,
                "score": result.score,
                "confidence": result.confidence,
                "reason": result.reason,
                "stats": result.stats,
                "simulation": result.simulation,
            },
        )

    async def _publish_batch_completed(self, summary: BacktestBatchResult) -> None:
        await self.event_bus.publish(
            f"{events.BACKTEST_COMPLETED}:{summary.strategy_name}",
            summary.as_dict(),
        )

    def _heartbeat(
        self,
        strategy_name: str,
        symbol: str | None,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        key = f"{self.name}:{strategy_name}:{symbol or 'TOP20'}"
        self.heartbeats[key] = datetime.now(timezone.utc).isoformat()
        self.health_bus.update(
            key,
            status,
            {
                "strategy_name": strategy_name,
                "symbol": symbol,
                **(payload or {}),
            },
        )

    # -------------------------------------------------
    # Helpers
    # -------------------------------------------------

    def _timeout_for(self, payload: dict[str, Any], *, single_symbol: bool) -> float:
        if single_symbol:
            return float(self.per_symbol_timeout)
        top_n = int(payload.get("top_n", 20) or 20)
        return max(float(self.per_symbol_timeout) * max(top_n, 1), 120.0)

    def _should_run_bootstrap(self) -> bool:
        now = datetime.now(timezone.utc)
        last = self.state_manager.last_backtest_run

        if self.run_on_first_start and last is None:
            return True

        if self.run_if_no_enabled:
            if self._has_enabled_symbols():
                self._no_enabled_forced = False
            elif not self._no_enabled_forced:
                self._no_enabled_forced = True
                return True

        if last is None:
            return False

        return now - last >= timedelta(days=90)

    def _should_run(self) -> bool:
        return self._should_run_bootstrap()

    def _has_enabled_symbols(self) -> bool:
        return any(sym.enabled for sym in (self.state_manager.bot.symbols or []))

    def _apply_backtest_scores(self) -> None:
        updated = False
        activated = []

        for sym in self.state_manager.bot.symbols:
            desired = None
            if isinstance(sym.meta, dict) and "desired_enabled" in sym.meta:
                desired = bool(sym.meta.get("desired_enabled"))

            should_enable = desired if desired is not None else (
                sym.score is not None and sym.score >= 0.75
            )

            if should_enable and not sym.enabled:
                sym.enabled = True
                activated.append(sym.symbol)
                updated = True

            if not should_enable and sym.enabled and desired is False:
                sym.enabled = False
                updated = True

        if updated:
            StateManager.save_bot_state(self.state_manager.bot)
            self.symbol_watch.refresh()
            logger.info("Activated %d symbols after backtest.", len(activated))
            if activated:
                logger.info("Activated symbols: %s", ", ".join(activated))
