import logging
from pathlib import Path
import json
import sys
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import threading
from typing import Optional

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
        stateManager: StateManager
    ):
        self.client = client
        self.cache = cache_handler
        self.registry = registry
        self.registry.register("backtest_data")
        self.registry.register("symbols")

        self.health_bus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event

        self.next_cycle = datetime.date()
        self.scheduler = ProcessScheduler(registry)
        self.botState = botState
        self._state = stateManager

    def load_last_backtest_time(self) -> datetime | None:
        if not STATE_FILE.exists():
            return None

        with open(STATE_FILE, "r") as f:
            data: dict = json.load(f)
            return datetime.fromisoformat(data.get("bot_configs")("last_backtest"))

    def save_last_backtest_time(self, ts: datetime):
        with open(STATE_FILE, "w") as f:
            json.dump({"last_backtest": ts.isoformat()}, f, indent=4)

    # -------------------------
    # Backtesting Logic
    # -------------------------
    def backtest_all_symbols(self, symbols: list[str]) -> dict:
        """
        Backtest all symbols and return performance registry.
        """

        results = {}

        for symbol in symbols:
            try:
                data = self.client.get_multi_tf_data(symbol)
                indicator = MA.MovingAverageCrossover(symbol, data)
                indicator.backtest = True
                data = indicator.run()
                results[symbol] = data

            except Exception as e:
                logger.warning(f"Backtest failed for {symbol}: {e}")
        indicator.backtest = False
        return results

    def select_best_symbols(self, results: dict, min_score=0.78) -> list:
        """
        Select best-performing symbols based on backtest metrics.
        """
        symbols = metrics.metrics.rank_symbols(results.get("summaries"), results.get("data"))
        for symbol in symbols:
            sym_score = symbol.get("summaries")("score")
            if sym_score <= min_score:
                symbols.remove(symbol)
                state = SymbolState(symbol, sym_score, datetime.now())
                self.botState.symbols.update({symbol: state})
            else:
                self.botState.symbols.update({symbol: SymbolState(symbol, sym_score, datetime.now(), enabled=True)})
        return symbols

    def run_backtest_cycle(self):
        """
        Run quarterly backtest and update active symbols.
        """
        now = datetime.now()
        last_run = self._state.last_backtest_run
        if last_run and now < last_run + relativedelta(months=3):
            return  # Too early
        try:
            logger.info(f"Starting scheduled backtest cycle. date now: {now}, next cycle: {self.next_cycle}")
            self._state.set_state(BotState.state.RUNNING_BACKTEST)

            results = self.backtest_all_symbols(self.client.symbols)
            best_symbols = self.select_best_symbols(results=results)

            # Activate only best symbols
            self.client.symbols.clear()
            self.client.symbols = best_symbols

            # cache best performing symbol data
            for sym in self.client.symbols:
                self.cache.set(sym, results[sym]["data"])

            self.registry.set_ready("backtest_data")
            self.registry.set_ready("symbols")

            self.heartbeats["backtest"] = now
            self.health_bus.update(
                self.name,
                "RUNNING",
                {"symbols": len(self.cache)}
            )

            self._state.set_state(BotState.state.RUNNING)
            self._state.schedule_next_backtest()
            logger.info(f"Backtest cycle completed. Next cycle scheduled for {self.next_cycle}.")
        except Exception as e:
            self.health_bus.update(self.name, "CRASHED", {"ERROR": str(e)})
            raise

    # -------------------------------
    # Safety Wrapper
    # -------------------------------
    def _backtest_safe_execute(self) -> Optional[dict]:
        try:
            return self.scheduler.schedule(
                self.name,
                BACKTEST_REQS,
                self.run_backtest_cycle,
                self.stop_event,
                self.heartbeats,
                shutdown_event=self.stop_event,
                timeout=60
            )
        except Exception as e:
            self._state.set_state(BotState.state.DEGRADED)
            logger.critical(f"Backtest process fail: {e}", exc_info=True)
            raise

    def stop(self):
        self.stop_event.clear()
