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
        per_symbol_timeout: float = 120.0,
        max_concurrent: int = 4,
    ):
        self.client = client
        self.cache = cache_handler
        self.symbol_watch = symbol_watch
        self.backtest = Backtest(self.client, self.cache, self.symbol_watch)

        self.scheduler = scheduler
        self.health_bus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event
        self.state_manager = state_manager
        self.event_bus = event_bus

        self.per_symbol_timeout = per_symbol_timeout
        self.max_concurrent = max_concurrent

        self._running = False  # single-flight guard

    # -------------------------------------------------
    # Registration (NO LOOP)
    # -------------------------------------------------

    def register(self):
        """
        Trigger backtest on system ready or explicit request.
        """
        self.event_bus.subscribe(
            events.MARKET_DATA_READY,
            lambda evt: asyncio.create_task(self._trigger_backtest())
        )

        self.event_bus.subscribe(
            events.RUN_BACKTEST,
            lambda evt: asyncio.create_task(self._trigger_backtest(force=True))
        )

    # -------------------------------------------------
    # Trigger
    # -------------------------------------------------

    async def _trigger_backtest(self, force: bool = False):
        if self.stop_event.is_set():
            return

        if self._running:
            return

        if not force and not self._should_run():
            return

        self._running = True

        try:
            await self.scheduler.schedule(
                process_name=self.name,
                required_resources=[],  # event-driven → no gating
                task=self._backtest_cycle,
                shutdown_event=self.stop_event,
                heartbeats=self.heartbeats,
                timeout=900,
            )
        finally:
            self._running = False

    # -------------------------------------------------
    # Core Logic (UNCHANGED BUT CLEANED)
    # -------------------------------------------------

    def _should_run(self) -> bool:
        now = datetime.now(timezone.utc)
        last = self.state_manager.last_backtest_run
        return not last or (now - last >= timedelta(days=90))

    async def _backtest_cycle(self):
        now = datetime.now(timezone.utc)
        self.state_manager.set_state(BotLifecycle.RUNNING_BACKTEST)

        self.backtest.initialise()

        errors = 0

        def _on_symbol(symbol: str, ok: bool):
            nonlocal errors

            self.heartbeats[self.name] = datetime.now(timezone.utc).isoformat()

            self.health_bus.update(
                self.name,
                "RUNNING",
                {
                    "phase": "backtesting",
                    "symbol": symbol,
                    "ok": ok,
                },
            )

            if not ok:
                errors += 1
            try:
                asyncio.create_task(
                    self.event_bus.publish(
                        f"{events.BACKTEST_COMPLETED}:{symbol}",
                        {"symbol": symbol, "ok": ok},
                    )
                )
            except Exception:
                logger.exception("Failed to emit backtest completion for %s", symbol)

        await self.backtest.run_once(
            on_symbol=_on_symbol,
            per_symbol_timeout=self.per_symbol_timeout,
            max_concurrent=self.max_concurrent,
        )

        # Persist state
        if errors == 0:
            self.state_manager.last_backtest_run = now
            StateManager.save_bot_state(self.state_manager.bot)

        self._apply_backtest_scores()

        self.state_manager.set_state(BotLifecycle.RUNNING)

        # 🔥 Event-driven signal
        await self.event_bus.publish(
            events.BACKTEST_COMPLETED,
            {
                "symbols": self.symbol_watch.all_symbol_names(),
                "symbol_count": len(self.symbol_watch.all_symbols),
                "telemetry": self.symbol_watch.snapshot(),
            },
        )

        self.health_bus.update(
            self.name,
            "RUNNING",
            {
                "symbols": len(self.symbol_watch.all_symbols),
            },
        )
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
