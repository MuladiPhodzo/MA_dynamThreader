import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from advisor.Client.mt5Client import MetaTrader5Client
from advisor.core.health_bus import HealthBus
from advisor.core.event_bus import EventBus
from advisor.core import events
from advisor.core.state import BotLifecycle, StateManager
from advisor.scheduler.requirements import ProcessRequirement
from advisor.utils.dataHandler import CacheManager
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.backtest.core import Backtest
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
    name = "backtest"

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
    ):
        self.client = client
        self.cache = cache_handler
        self.symbol_watch = symbol_watch
        self.backtest = Backtest(self.client, self.cache, self.symbol_watch)

        self.scheduler = scheduler
        self.registry = registry
        self.registry.register("backtest")
        self.health_bus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event
        self.state_manager = state_manager
        self.event_bus = event_bus

        self.per_symbol_timeout = per_symbol_timeout
        self.max_concurrent = max_concurrent

        self._running: set[str] = set()  # prevent duplicate runs per symbol
        self._subscribed_symbols: set[str] = set()

    # -------------------------------------------------
    # Registration (NO LOOP)
    # -------------------------------------------------
    def register(self):
        self.event_bus.subscribe(events.MARKET_DATA_READY, self._on_symbols)

        for s in self.symbol_watch.all_symbol_names():
            self._subscribe(s)

    def _subscribe(self, symbol: str):
        if symbol in self._subscribed_symbols:
            return

        self._subscribed_symbols.add(symbol)

        self.event_bus.subscribe(
            f"{events.MARKET_DATA_READY}:{symbol}",
            lambda evt, s=symbol: asyncio.create_task(self._trigger(s)),
        )

    def _on_symbols(self, event):
        symbols = event.payload.get("symbols") if event and event.payload else None
        if not symbols:
            symbols = self.symbol_watch.all_symbol_names()

        for s in symbols:
            self._subscribe(s)

    # -------------------------------------------------
    # Trigger
    # -------------------------------------------------

    async def _trigger(self, symbol: str):
        if self.stop_event.is_set():
            return

        if symbol in self._running:
            return

        if not self._should_run():
            return

        self._running.add(symbol)

        try:
            await self.scheduler.schedule(
                process_name=f"{self.name}:{symbol}",
                required_resources=[],
                task=lambda: self._run(symbol),
                shutdown_event=self.stop_event,
                heartbeats=self.heartbeats,
                timeout=120,
            )
        finally:
            self._running.discard(symbol)

    # -------------------------------------------------
    # Core Execution
    # -------------------------------------------------

    async def _run(self, symbol: str):
        self.state_manager.set_state(BotLifecycle.RUNNING_BACKTEST)

        errors = 0

        def _on_complete(sym: str, ok: bool):
            nonlocal errors

            self.heartbeats[f"{self.name}:{sym}"] = datetime.now(timezone.utc).isoformat()

            self.health_bus.update(
                f"{self.name}:{sym}",
                "RUNNING",
                {"symbol": sym, "ok": ok},
            )

            if not ok:
                errors += 1

            asyncio.create_task(
                self.event_bus.publish(
                    f"{events.BACKTEST_COMPLETED}:{sym}",
                    {"symbol": sym, "ok": ok},
                )
            )

        ok = await self.backtest.run_symbol(symbol, _on_complete)

        if ok:
            self._update_state()

        self.state_manager.set_state(BotLifecycle.RUNNING)

    # -------------------------------------------------
    # Helpers
    # -------------------------------------------------

    def _should_run(self) -> bool:
        now = datetime.now(timezone.utc)
        last = self.state_manager.last_backtest_run
        return not last or (now - last >= timedelta(days=90))

    def _update_state(self):
        self.state_manager.last_backtest_run = datetime.now(timezone.utc)
        StateManager.save_bot_state(self.state_manager.bot)
    # -------------------------------------------------
    # Helpers (UNCHANGED)
    # -------------------------------------------------

    def _apply_backtest_scores(self):
        updated = False
        activated = []

        for sym in self.state_manager.bot.symbols:
            desired = None
            if isinstance(sym.meta, dict) and "desired_enabled" in sym.meta:
                desired = bool(sym.meta.get("desired_enabled"))
                sym.meta.pop("desired_enabled", None)

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
