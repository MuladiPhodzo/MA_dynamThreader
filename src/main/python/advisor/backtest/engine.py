import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from advisor.core.health_bus import HealthBus
from advisor.core.state import BotLifecycle, StateManager
from advisor.scheduler.process_sceduler import ProcessScheduler
from advisor.scheduler.requirements import ProcessRequirement
from advisor.scheduler.resource_registry import ResourceRegistry
from advisor.utils.dataHandler import CacheManager
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.backtest.core import Backtest
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("MA_DynamAdvisor.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("Backtest")

STATE_FILE = Path("bot_state.json")
BACKTEST_REQS = [ProcessRequirement("market_data", max_age=timedelta(minutes=10))]


class backtestProcess:
    name = "backtest"

    def __init__(
        self,
        client,
        cache_handler: CacheManager,
        registry: ResourceRegistry,
        health_bus: HealthBus,
        heartbeats: dict,
        shutdown_event,
        bot_state,
        state_manager: StateManager,
        scheduler: ProcessScheduler,
        symbol_watch: SymbolWatch,
    ):
        self.client = client
        self.cache = cache_handler
        self.backtest = Backtest(self.client, self.cache, self.symbol_watch)
        self.symbol_watch = symbol_watch
        self.registry = registry
        self.health_bus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event
        self.scheduler = scheduler
        self.bot_state = bot_state
        self.state_manager = state_manager

        self.registry.register("backtest_data")
        self.registry.register("symbols")

    def _load_last_backtest_time(self) -> datetime | None:
        if not STATE_FILE.exists():
            return None
        try:
            raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            ts = raw.get("last_backtest_run")
            return datetime.fromisoformat(ts) if ts else None
        except Exception:
            return None

    def _save_last_backtest_time(self, ts: datetime):
        STATE_FILE.write_text(json.dumps({"last_backtest_run": ts.isoformat()}, indent=2), encoding="utf-8")

    async def _backtest_cycle(self):
        now = datetime.now(timezone.utc)
        last_run = self._load_last_backtest_time() or self.state_manager.last_backtest_run
        if last_run and now - last_run < timedelta(days=90):
            return

        self.state_manager.set_state(BotLifecycle.RUNNING_BACKTEST)
        self.backtest.__run_loop()
        self.registry.set_ready("backtest_data")
        self.registry.set_ready("symbols")
        self._save_last_backtest_time(now)
        self.state_manager.last_backtest_run = now
        StateManager.save_bot_state(self.state_manager.bot)
        self._activate_symbols_after_backtest()
        self.state_manager.set_state(BotLifecycle.RUNNING)
        self.health_bus.update(
            self.name,
            "RUNNING",
            {
                "active_symbols": len(self.symbol_watch.all_symbols),
                "telemetry": self.symbol_watch.snapshot(),
            },
        )

    def _activate_symbols_after_backtest(self) -> None:
        # Only activate if none are currently enabled.
        if any(sym.enabled for sym in self.state_manager.bot.symbols):
            return

        activated = []
        for sym in self.state_manager.bot.symbols:
            desired = False
            if isinstance(sym.meta, dict):
                desired = bool(sym.meta.get("desired_enabled", False))
            if desired:
                sym.enabled = True
                activated.append(sym.symbol)

        if activated:
            StateManager.save_bot_state(self.state_manager.bot)
            self.symbol_watch.refresh()
            logger.info(
                "Activated %d symbols after backtest.",
                len(activated),
            )

    async def _run_loop(self):
        while not self.stop_event.is_set():
            await self.scheduler.schedule(
                process_name=self.name,
                required_resources=BACKTEST_REQS,
                task=self._backtest_cycle,
                shutdown_event=self.stop_event,
                heartbeats=self.heartbeats,
                timeout=600,
            )
            await asyncio.sleep(5)

    def start(self):
        try:
            asyncio.run(self._run_loop())
        except Exception as e:
            self.state_manager.set_state(BotLifecycle.DEGRADED)
            logger.critical("%s crashed: %s", self.name, e, exc_info=True)
            self.health_bus.update(self.name, "CRASHED", {"error": str(e)})
            raise
