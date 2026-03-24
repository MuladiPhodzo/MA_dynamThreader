import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone

import advisor.mt5_pipeline.core as core
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.core.health_bus import HealthBus
from advisor.core.event_bus import EventBus
from advisor.core import events
from advisor.core.state import BotLifecycle, StateManager
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
    ):
        self.cache = cache_handler
        self.client = client
        self.poll_interval = interval
        self.last_run: datetime | None = None
        self.state = state_manager
        self.symbol_watch = symbol_watch

        self.registry = registry
        self.health_bus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event
        self.registry.register("market_data")
        self.registry.register("symbol_ingestion")
        self.scheduler = scheduler
        self.event_bus = event_bus
        self.pipeline = core.MarketDataPipeline(self.client, self.cache, self.symbol_watch)
        self.state_store = state_store
        self.per_symbol_timeout = per_symbol_timeout
        self.max_concurrent = max_concurrent

        if self.state_store is not None:
            section = self.state_store.get_section("pipeline", {})
            if isinstance(section, dict):
                ts = section.get("last_run")
                if ts:
                    try:
                        self.last_run = datetime.fromisoformat(ts)
                    except Exception:
                        pass

    async def _pipeline_cycle(self):
        now = datetime.now(timezone.utc)
        if self.last_run and now - self.last_run < timedelta(minutes=self.poll_interval):
            return

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
        self.last_run = now
        self.registry.set_ready("market_data")
        self.registry.set_ready("symbol_ingestion")
        if self.state_store is not None:
            self.state_store.update_section("pipeline", {"last_run": now.isoformat()})
        if self.event_bus is not None:
            self.event_bus.emit(
                events.MARKET_DATA_READY,
                {
                    "symbols": len(self.symbol_watch.active_symbols),
                    "telemetry": self.symbol_watch.snapshot(),
                },
            )
        self.health_bus.update(
            self.name,
            "RUNNING",
            {
                "symbols": len(self.symbol_watch.active_symbols),
                "telemetry": self.symbol_watch.snapshot(),
            },
        )

    async def _run_loop(self):
        loop = asyncio.get_running_loop()
        tick = asyncio.Event()

        def schedule_tick():
            if self.stop_event.is_set():
                return
            tick.set()
            loop.call_later(self.poll_interval, schedule_tick)

        schedule_tick()

        while not self.stop_event.is_set():
            try:
                await asyncio.wait_for(tick.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            tick.clear()
            symbol_count = len(self.symbol_watch.active_symbol_names() or self.symbol_watch.all_symbol_names())
            per_symbol_budget = max(5.0, float(self.per_symbol_timeout))
            concurrency = max(1, int(self.max_concurrent))
            estimated = int((symbol_count / concurrency) * per_symbol_budget) + 60
            timeout = max(180, estimated)
            logger.info(f"estimated timeout {self.name} >> {timeout}")
            
            await self.scheduler.schedule(
                process_name=self.name,
                required_resources=[],
                task=self._pipeline_cycle,
                shutdown_event=self.stop_event,
                heartbeats=self.heartbeats,
                timeout=timeout,
            )

    def start(self):
        try:
            asyncio.run(self._run_loop())
        except Exception as e:
            self.state.set_state(BotLifecycle.DEGRADED)
            logger.critical("%s crashed: %s", self.name, e, exc_info=True)
            self.health_bus.update(self.name, "CRASHED", {"error": str(e)})
            raise
