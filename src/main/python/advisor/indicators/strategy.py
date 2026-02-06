from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Optional

from advisor.indicators.MovingAverage.MovingAverage import MovingAverageCrossover
from advisor.indicators.Volume.volumeindex import VolumeIndex
from advisor.utils.dataHandler import dataHandler
from advisor.mt5_pipeline.Client.mt5Client import MetaTrader5Client

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
    """
        Orchestrates strategy execution across all symbols.
    """
    def __init__(
        self,
        client: MetaTrader5Client,
        max_workers: int = 8
    ):
        self.symbols = client.symbols
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

        self.symbol_managers: Dict[str, SymbolStrategyManager] = {}

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

            manager.register(
                "VOLUME",
                VolumeIndex(
                    dataHandler(symbol, "VOLUME")
                )
            )

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

        return results if results else None

    # -------------------------------
    # Safety Wrapper
    # -------------------------------
    @staticmethod
    def _safe_execute(symbol: str, strategy: object) -> Optional[dict]:
        try:
            return strategy.run()
        except Exception as e:
            # isolate symbol failure
            print(f"[STRATEGY ERROR] {symbol}: {e}")
            return None

