import sys
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from typing import Dict
import threading
from datetime import datetime, timedelta
import asyncio

from advisor.indicators.MovingAverage.MovingAverage import MovingAverageCrossover
# from advisor.indicators.Volume.volumeindex import VolumeIndex
from advisor.utils.dataHandler import dataHandler
from advisor.Client.mt5Client import MetaTrader5Client
from advisor.scheduler.resource_registry import ResourceRegistry
from advisor.core.health_bus import HealthBus
from advisor.scheduler.requirements import ProcessRequirement
from advisor.scheduler.process_sceduler import ProcessScheduler
from .signal_store import SignalStore
from advisor.core.state import BotState, StateManager


STRATEGY_REQS = [
    ProcessRequirement("market_data", max_age=timedelta(minutes=5)),
    ProcessRequirement("backtest", max_age=timedelta(days=90)),
    ProcessRequirement("symbols", max_age=timedelta(days=90))
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

logger = logging.getLogger("Strategy_Manager")
class SymbolStrategyManager:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.strategies: dict[str, object] = {}

    def register(self, name: str, strategy_instance: object):
        self.strategies[name] = strategy_instance

    def get(self, name: str):
        return self.strategies.get(name)

    def available_strategies(self) -> list[str]:
        return list(self.strategies.keys())

class strategyManager:
    name = "Strategy"
    """
        Orchestrates strategy execution across all symbols.
    """
    def __init__(
        self,
        client: MetaTrader5Client,
        shutdown_event: threading.Event,
        heartbeats: dict,  # shared heartbeat state(Authorative)
        health_bus: HealthBus,  # shared health bus state(Authorative)
        registry: ResourceRegistry,
        scheduler: ProcessScheduler,
        store: SignalStore,
        state: StateManager,
        max_workers: int = 5,
        interval=5,
    ):
        self.symbols = client.symbols
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

        self.registry = registry
        self.registry.register("signals")
        self.healthbus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event
        self.symbol_managers: Dict[str, SymbolStrategyManager] = {}
        self.scheduler = scheduler
        self.state = state
        self.signal_store = store
        self.interval = interval
        self._init_symbols()

    def shutdown(self):
        self.executor.shutdown(wait=False)

    def _init_symbols(self):
        """_summary_
            initialize a symbol strategy, under a new strategy instance with its own isolated data handler instance
        Args:
            symbol (string): the symbol to which the strategy will be implemented
            strategy_instance (class instance): new startegy instance which the symbol be analysed through.
        """
        for symbol in self.symbols:
            manager = SymbolStrategyManager(symbol)
            manager.register(
                "MA",
                MovingAverageCrossover(
                    symbol=symbol,
                    data=dataHandler(symbol, "EMA"),
                    heartbeats=self.heartbeats,
                    healthbus=self.healthbus
                )
            )
            logger.info(f"MA startegy initialized for {symbol}")
            # add add other strategies in the future
            # manager.register(
            #     "VOLUME",
            #     VolumeIndex(
            #         dataHandler(symbol, "VOLUME"),
            #         heartbeats=self.heartbeats,
            #         healthbus=self.healthbus
            #     )
            # )

            self.symbol_managers[symbol] = manager
        pass

    # -------------------------------
    # Execution
    # -------------------------------

    def start(self):
        try:
            asyncio.run(self._safe_execute())
            time.sleep(self.interval)
        except Exception as e:
            self.state.set_state(BotState.state.DEGRADED)
            logger.critical(f"{self.name} crashed: {e}", exc_info=True)
            self.healthbus.update(
                self.name,
                "CRASHED",
                {"error": str(e)}
            )
            raise

    async def _safe_execute(self):
        while not self.stop_event.is_set():
            await self.scheduler.schedule(
                process_name=self.name,
                required_resources=STRATEGY_REQS,
                task=await self._run_cycle,
                shutdown_event=self.stop_event,
                heartbeats=self.heartbeats,
                timeout=60
            )
            asyncio.sleep(self.interval)

    async def _run_cycle(self):
        results = {}
        symbols_ftr = {}

        for symbol, manager in self.symbol_managers.items():
            symbols_ftr[self.executor.submit(self.run_strategy, manager)] = symbol

        for s in as_completed(symbols_ftr):
            res = s.result()
            results[symbol] = res  # process signals from response

        self.heartbeats[self.name] = datetime.now(datetime.timezone.utc).isoformat()

        self.healthbus.update(
            self.name,
            "RUNNING",
            {"symbols": len(self.symbol_managers)}
        )

        return results

    async def run_strategy(symbol, manager: SymbolStrategyManager):
        results = {}
        for strategy_name in manager.available_strategies():
            strategy = manager.get(strategy_name)

            try:
                result = strategy.run()  # all strategies must expose run() method
                if result:
                    results.setdefault(symbol, {})[strategy_name] = result
            except Exception as e:
                logger.error(
                    f"{symbol} {strategy_name} failed: {e}",
                    exc_info=True
                )
