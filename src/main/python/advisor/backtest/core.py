import asyncio
from typing import Callable

from advisor.Client.mt5Client import MetaTrader5Client
from advisor.indicators.MA.MovingAverage import MovingAverageCrossover
from advisor.utils.dataHandler import CacheManager
from advisor.backtest.metrics import metrics
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.core.state import Strategy, SymbolState
from advisor.utils.logging_setup import get_logger

logger = get_logger(__name__)

class Backtest:
    """
    Fully event-driven, per-symbol backtest engine.
    - No global loops
    - No eager initialization
    - Data-aware execution
    """

    def __init__(self, client: MetaTrader5Client, cache: CacheManager, symbol_watch: SymbolWatch):
        self.client = client
        self.cache = cache
        self.symbol_watch = symbol_watch

    # -------------------------------------------------
    # Public API
    # -------------------------------------------------

    async def run_symbol(
        self,
        symbol: str,
        on_complete: Callable[[str, bool], None] | None = None,
    ) -> bool:
        """
        Run backtest for a single symbol.
        """
        sym = self.symbol_watch.get(symbol)
        if sym is None:
            return False

        if not self._has_data(symbol):
            logger.debug(f"{symbol}: skipping (no data)")
            return False

        self._ensure_strategy(sym)

        produced = False

        for strat in sym.strategies:
            try:
                logger.info(f"[Backtest] Running {symbol} ({strat.strategy_name})")

                await asyncio.to_thread(strat.strategy, True)

                results = getattr(strat.strategy, "results", None)

                if not results:
                    logger.warning(f"{symbol}: no results")
                    continue

                stats = self._extract_stats(results)
                if not isinstance(stats, dict):
                    continue

                metric = metrics(sym)
                sym.score = metric.compute_symbol_score(stats)

                produced = True

            except Exception as e:
                logger.exception(f"{symbol}: strategy failed → {e}")

        if on_complete:
            on_complete(symbol, produced)

        return produced

    # -------------------------------------------------
    # Helpers
    # -------------------------------------------------

    def _has_data(self, symbol: str) -> bool:
        data = self.cache.get(symbol)
        return bool(data)

    def _ensure_strategy(self, sym: SymbolState):
        name = f"{sym.symbol}_EMA"

        if any(s.strategy_name == name for s in sym.strategies):
            return

        strategy = MovingAverageCrossover(
            sym,
            self.client,
            self.cache,
            start_workers=False,
        )

        sym.strategies.append(
            Strategy(strategy_name=name, strategy=strategy, strategy_score=0.0)
        )

    def _extract_stats(self, results: dict):
        stats = results.get("15M") or results.get("30M")
        if stats is None and results:
            stats = next(iter(results.values()))
        return stats
