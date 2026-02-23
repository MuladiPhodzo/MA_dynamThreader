import logging
import asyncio
import sys
from pathlib import Path
import json
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import threading

from advisor.Client.mt5Client import MetaTrader5Client
from advisor.indicators.MovingAverage import MovingAverage as MA
from advisor.utils.dataHandler import CacheManager
from advisor.scheduler.resource_registry import ResourceRegistry
from advisor.scheduler.requirements import ProcessRequirement
from advisor.core.health_bus import HealthBus
from scheduler.process_sceduler import ProcessScheduler
from advisor.core.state import SymbolState, StateManager, BotState
from . import metrics

BACKTEST_REQS = [
    ProcessRequirement("market_data", max_age=timedelta(minutes=5))
]
# -------------------------
# Logging Configuration
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("MA_DynamAdvisor.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)

STATE_FILE = Path("config.json")
BACKTEST_INTERVAL_DAYS = 90  # ≈ 3 months
class backtestProcess:
    name = "Backtest_Process"

    def __init__(
        self,
        client: MetaTrader5Client,
        cache_handler: CacheManager,
        registry: ResourceRegistry,
        health_bus: HealthBus,
        heartbeats: dict,
        shutdown_event: threading.Event,
        botState: BotState,
        stateManager: StateManager,
        scheduler: ProcessScheduler,
    ):
        self.client = client
        self.cache = cache_handler
        self.registry = registry

        self.registry.register("backtest_data")
        self.registry.register("symbols")

        self.health_bus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event

        self.scheduler = scheduler
        self.botState = botState
        self._state = stateManager

    def load_last_backtest_time(self) -> datetime | None:
        if not STATE_FILE.exists():
            return None

        with open(STATE_FILE, "r") as f:
            data = json.load(f)

        ts = data.get("last_backtest")
        return datetime.fromisoformat(ts) if ts else None

    def save_last_backtest_time(self, ts: datetime):
        with open(STATE_FILE, "w") as f:
            json.dump({"last_backtest": ts.isoformat()}, f, indent=4)

    # -------------------------
    # Backtesting Logic
    # -------------------------
    async def _backtest_all_symbols(self, symbols: list[str]) -> dict:

        results = {}

        for symbol in symbols:
            try:
                data = await asyncio.to_thread(
                    self.client.get_multi_tf_data,
                    symbol
                )

                indicator = MA.MovingAverageCrossover(symbol, data)
                indicator.backtest = True

                output = await asyncio.to_thread(indicator.run)

                results[symbol] = output

            except Exception as e:
                logger.warning(f"Backtest failed for {symbol}: {e}")

        return results

    def _select_best_symbols(self, results: dict, min_score=0.78) -> list:

        ranked = metrics.metrics.rank_symbols(
            results.get("summaries"),
            results.get("data")
        )

        selected = []

        for symbol_data in ranked:
            sym = symbol_data["symbol"]
            score = symbol_data["score"]

            new_state = SymbolState(sym, score, datetime.now(datetime.timezone.utc), True)
            self.botState.symbols[sym] = new_state

        return selected

    async def _backtest_cycle(self):
        now = datetime.now(datetime.timezone.utc)
        last_run = self._state.last_backtest_run

        if last_run and now < last_run + relativedelta(months=3):
            return  # Not time yet

        logger.info("Starting scheduled backtest cycle")

        self._state.set_state(BotState.state.RUNNING_BACKTEST)
        self.botState.backtest_running = True

        results = await self._backtest_all_symbols(self.client.symbols)

        best_symbols = self._select_best_symbols(results)

        # Update active symbols safely
        self.client.symbols = list(best_symbols)

        # Cache best performing data
        for sym in best_symbols:
            self.cache.set_atomic(sym, results[sym]["data"])

        self.registry.set_ready("backtest_data")
        self.registry.set_ready("symbols")

        self.save_last_backtest_time(now)

        self._state.set_state(BotState.state.RUNNING)
        self.botState.backtest_running = False
        self.health_bus.update(
            self.name,
            "RUNNING",
            {"active_symbols": len(best_symbols)}
        )

        logger.info("Backtest cycle completed")

    # -------------------------------
    # Safety Wrapper
    # -------------------------------
    async def _run_loop(self):

        while not self.stop_event.is_set():

            await self.scheduler.schedule(
                process_name=self.name,
                required_resources=BACKTEST_REQS,
                task=self._backtest_cycle,
                shutdown_event=self.stop_event,
                heartbeats=self.heartbeats,
                timeout=600,  # backtest may take longer
            )
            
            await asyncio.sleep(5)

    def start(self):
        try:
            asyncio.run(self._run_loop())
        except Exception as e:
            self._state.set_state(BotState.state.DEGRADED)
            logger.critical(f"{self.name} crashed: {e}", exc_info=True)
            self.health_bus.update(
                self.name,
                "CRASHED",
                {"error": str(e)}
            )
            raise
