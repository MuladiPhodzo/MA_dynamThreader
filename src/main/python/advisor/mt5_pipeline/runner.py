import asyncio
from contextlib import suppress
from datetime import datetime, timezone

import advisor.mt5_pipeline.core as core
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.core.health_bus import HealthBus
from advisor.core.event_bus import EventBus
from advisor.core import events
from advisor.core.state import StateManager
from advisor.scheduler.process_sceduler import ProcessScheduler
from advisor.scheduler.resource_registry import ResourceRegistry
from advisor.utils.dataHandler import CacheManager
from advisor.utils.logging_setup import get_logger

logger = get_logger("MT5-Pipeline")


class pipelineProcess:
    name = "pipeline"

    def __init__(
        self,
        client,
        cache_handler: CacheManager,
        shutdown_event,
        heartbeats: dict,
        health_bus: HealthBus,
        registry: ResourceRegistry,
        scheduler: ProcessScheduler,
        state_manager: StateManager,
        symbol_watch: SymbolWatch,
        interval=5,
        event_bus: EventBus | None = None,
        state_store=None,
        per_symbol_timeout: float = 120.0,
        max_concurrent: int = 6,
        max_symbol_errors: int = 3,
    ):
        self.cache = cache_handler
        self.client = client
        self.poll_interval = interval
        self.state = state_manager
        self.symbol_watch = symbol_watch

        self.registry = registry
        self.health_bus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event
        self.scheduler = scheduler
        self.event_bus = event_bus
        self.pipeline = core.MarketDataPipeline(self.client, self.cache, self.symbol_watch)
        self.per_symbol_timeout = per_symbol_timeout
        self.max_concurrent = max_concurrent
        self.max_symbol_errors = max(1, int(max_symbol_errors))

    async def _pipeline_cycle(self):

        logger.info("Running market data ingestion")
        stop_beat = asyncio.Event()

        async def _heartbeat_loop():
            while not stop_beat.is_set():
                stamp = datetime.now(timezone.utc).isoformat()
                self.heartbeats[self.name] = stamp
                self.health_bus.update(
                    self.name,
                    "RUNNING",
                    {
                        "phase": "ingesting",
                        "symbols": len(self.symbol_watch.active_symbols),
                    },
                )
                try:
                    await asyncio.wait_for(stop_beat.wait(), timeout=30)
                except asyncio.TimeoutError:
                    continue

        beat_task = asyncio.create_task(_heartbeat_loop())

        def _on_symbol(symbol: str, ok: bool) -> None:
            stamp = datetime.now(timezone.utc).isoformat()
            self.heartbeats[self.name] = stamp
            self.health_bus.update(
                self.name,
                "RUNNING",
                {
                    "phase": "ingesting",
                    "symbol": symbol,
                    "ok": ok,
                    "symbols": len(self.symbol_watch.active_symbols),
                },
            )
            if not ok:
                telem = self.symbol_watch.get_telemetry(symbol)
                if telem and telem.error_count >= self.max_symbol_errors:
                    self._disable_symbol(symbol, f"errors={telem.error_count}")

        try:
            await self.pipeline.run_once(
                on_symbol=_on_symbol,
                per_symbol_timeout=self.per_symbol_timeout,
                max_concurrent=self.max_concurrent,
            )
        finally:
            stop_beat.set()
            beat_task.cancel()
            with suppress(asyncio.CancelledError):
                await beat_task
        payload = {
            "symbols": len(self.symbol_watch.active_symbols),
            "telemetry": self.symbol_watch.snapshot(),
        }
        await self.event_bus.publish(
            events.MARKET_DATA_READY,
            payload,
        )
        self.health_bus.update(
            self.name,
            "RUNNING",
            payload
        )

    def register(self):
        if self.event_bus:
            self.event_bus.subscribe(events.SYMBOLS, self._on_market_tick)

    async def _on_market_tick(self, _):
        if self.stop_event.is_set():
            return

        if getattr(self, "_running", False):
            return  # prevent overlap

        self._running = True

        try:
            symbol_count = len(
                self.symbol_watch.active_symbol_names()
                or self.symbol_watch.all_symbol_names()
            )

            per_symbol_budget = max(5.0, float(self.per_symbol_timeout))
            concurrency = max(1, int(self.max_concurrent))
            estimated = int((symbol_count / concurrency) * per_symbol_budget) + 60
            timeout = max(180, estimated)

            await self.scheduler.schedule(
                process_name=self.name,
                required_resources=[],
                task=self._pipeline_cycle,
                shutdown_event=self.stop_event,
                heartbeats=self.heartbeats,
                timeout=timeout,
            )
        except Exception as e:
            logger.exception("Pipeline execution failed: %s", e)
            self.health_bus.update(self.name, "ERROR", {"error": str(e)})

        finally:
            self._running = False

    def _disable_symbol(self, symbol: str, reason: str) -> None:
        try:
            updated = False
            for sym in self.state.bot.symbols or []:
                if sym.symbol == symbol:
                    if sym.enabled:
                        sym.enabled = False
                        updated = True
                    if isinstance(sym.meta, dict):
                        sym.meta["auto_disabled"] = True
                        sym.meta["auto_disable_reason"] = reason
                    break
            if updated:
                StateManager.save_bot_state(self.state.bot)
                self.symbol_watch.refresh()
                logger.warning("Auto-disabled %s (%s)", symbol, reason)
        except Exception:
            logger.exception("Failed to auto-disable symbol %s", symbol)
