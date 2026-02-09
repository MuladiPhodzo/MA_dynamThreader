import sys
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Optional
import threading
from datetime import datetime, timedelta
from advisor.indicators.MovingAverage.MovingAverage import MovingAverageCrossover
# from advisor.indicators.Volume.volumeindex import VolumeIndex
from advisor.utils.dataHandler import dataHandler
from advisor.mt5_pipeline.Client.mt5Client import MetaTrader5Client
from advisor.scheduler.resource_registry import ResourceRegistry
from advisor.core.health_bus import HealthBus
from advisor.scheduler.requirements import ProcessRequirement
from advisor.scheduler.process_sceduler import ProcessScheduler


STRATEGY_REQS = [
    ProcessRequirement("market_data", max_age=timedelta(minutes=2)),
    ProcessRequirement("backtest", max_age=timedelta(days=90)),
    ProcessRequirement("symbols")
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
        max_workers: int = 8
    ):
        self.symbols = client.symbols
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

        self.registry = registry
        self.healthbus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event
        self.symbol_managers: Dict[str, SymbolStrategyManager] = {}
        self.scheduler = ProcessScheduler(registry)

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
                    data=dataHandler(symbol, "EMA")
                )
            )
            logger.info(f"MA startegy initialized for {symbol}")
            # add add other strategies in the future
            # manager.register(
            #     "VOLUME",
            #     VolumeIndex(
            #         dataHandler(symbol, "VOLUME")
            #     )
            # )

            self.symbol_managers[symbol] = manager
        pass

    # -------------------------------
    # Execution
    # -------------------------------

    def run_strategy(
        self,
        strategy_name: str
    ) -> Optional[Dict[str, dict]]:
        """
        Runs a single strategy across all symbols in parallel.
        """
        try:
            futures = {}
            results: Dict[str, dict] = {}

            for symbol, manager in self.symbol_managers.items():
                strategy = manager.get(strategy_name)
                if not strategy:
                    continue

                futures[self.executor.submit(
                    self._safe_execute,
                    symbol,
                    strategy
                )] = symbol

            for future in as_completed(futures):
                symbol = futures[future]
                result = future.result()

                if result is not None:
                    results[symbol] = result
                self.heartbeats[self.name] = datetime.utcnow().isoformat()
                self.healthbus.update(self.name, "RUNNING")
            return results
        except Exception as e:
            self.healthbus.update(self.name, "CRASHED", {"ERROR": str(e)})
            return None

    # -------------------------------
    # Safety Wrapper
    # -------------------------------
    def _safe_execute(self, symbol: str, strategy: object) -> Optional[dict]:
        try:
            return self.scheduler.schedule(
                self.name,
                STRATEGY_REQS,
                strategy.run(),
                self.stop_event,
                self.heartbeats
            )
        except Exception as e:
            logger.critical(f"{symbol} Backtest process fail: {e}", exc_info=True)
            raise
