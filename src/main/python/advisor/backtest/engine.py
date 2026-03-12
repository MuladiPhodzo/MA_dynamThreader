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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("MA_DynamAdvisor.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("Backtest")

STATE_FILE = Path("config.json")
BACKTEST_REQS = [ProcessRequirement("market_data", max_age=timedelta(minutes=10))]


class backtestProcess:
    name = "Backtest"

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
        self.registry = registry
        self.health_bus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event
        self.scheduler = scheduler
        self.bot_state = bot_state
        self.state_manager = state_manager
        self.symbol_watch = symbol_watch

        self.registry.register("backtest_data")
        self.registry.register("symbols")

    def _load_last_backtest_time(self) -> datetime | None:
        if not STATE_FILE.exists():
            return None
        try:
            raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            ts = raw.get("last_backtest")
            return datetime.fromisoformat(ts) if ts else None
        except Exception:
            return None

    def _save_last_backtest_time(self, ts: datetime):
        STATE_FILE.write_text(json.dumps({"last_backtest": ts.isoformat()}, indent=2), encoding="utf-8")

    async def _backtest_cycle(self):
        now = datetime.now(timezone.utc)
        last_run = self._load_last_backtest_time() or self.state_manager.last_backtest_run
        if last_run and now - last_run < timedelta(days=90):
            return

        self.state_manager.set_state(BotLifecycle.RUNNING_BACKTEST)
        symbols = self.symbol_watch.active_symbol_names()

        for sym in symbols:
            data = await asyncio.to_thread(self.client.get_multi_tf_data, sym)
            if data:
                self.cache.set(sym, data)
                self.symbol_watch.mark_data_fetch(sym)
            else:
                self.symbol_watch.mark_error(sym, "backtest fetch failed")

        self.registry.set_ready("backtest_data")
        self.registry.set_ready("symbols")
        self._save_last_backtest_time(now)
        self.state_manager.last_backtest_run = now
        self.state_manager.set_state(BotLifecycle.RUNNING)
        self.health_bus.update(
            self.name,
            "RUNNING",
            {
                "active_symbols": len(symbols),
                "telemetry": self.symbol_watch.snapshot(),
            },
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
