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
    Backtest core with a structure similar to mt5_pipeline.core.
    Provides per-symbol execution with safe error handling and an async run_once.
    """

    def __init__(self, client: MetaTrader5Client, cache_manager: CacheManager, symbol_watch: SymbolWatch):
        self.client = client
        self.cache = cache_manager
        self.symbol_watch = symbol_watch
        self.initialise()

    # -------------------------
    # Public API
    # -------------------------
    def run(self, symbol: SymbolState) -> bool:
        """
        Run backtest for a single symbol (sync).
        Returns True if any stats were produced.
        """
        return self._run_symbol(symbol)

    async def run_once(
        self,
        on_symbol: Callable[[str, bool], None] | None = None,
        per_symbol_timeout: float | None = None,
        max_concurrent: int | None = None,
        symbols: list[SymbolState] | None = None,
    ) -> None:
        """
        Run backtests across symbols with optional concurrency and timeout.
        """
        symbols = symbols or self.symbol_watch.all_symbols
        if not symbols:
            logger.warning("No symbols available for backtest.")
            return

        semaphore = asyncio.Semaphore(max_concurrent) if max_concurrent else None

        async def _wrap(sym: SymbolState):
            try:
                if semaphore:
                    async with semaphore:
                        ok = await self._backtest_with_timeout(sym, per_symbol_timeout)
                else:
                    ok = await self._backtest_with_timeout(sym, per_symbol_timeout)
                return sym, ok, None
            except Exception as exc:
                return sym, False, exc

        tasks = [asyncio.create_task(_wrap(sym)) for sym in symbols]
        for task in asyncio.as_completed(tasks):
            sym, ok, err = await task
            self._process_task_result(sym, ok, err, on_symbol)

    # -------------------------
    # Internal helpers
    # -------------------------
    def _run_symbol(self, symbol: SymbolState) -> bool:
        try:
            self._ensure_symbol_strategy(symbol)
            return self._run_loop(symbol)
        except Exception as e:
            logger.exception("Backtest failed for %s: %s", getattr(symbol, "symbol", symbol), e)
            self.symbol_watch.mark_error(getattr(symbol, "symbol", str(symbol)), f"backtest failed: {e}")
            return False

    async def _backtest_with_timeout(self, symbol: SymbolState, timeout: float | None) -> bool:
        if timeout:
            return await asyncio.wait_for(asyncio.to_thread(self._run_symbol, symbol), timeout=timeout)
        return await asyncio.to_thread(self._run_symbol, symbol)

    def _process_task_result(
        self,
        symbol: SymbolState,
        ok: bool,
        err: Exception | None,
        on_symbol: Callable[[str, bool], None] | None,
    ) -> None:
        name = getattr(symbol, "symbol", str(symbol))
        if err is not None:
            logger.error("Backtest task failed for %s: %s", name, err)
            self.symbol_watch.mark_error(name, "backtest failed")
            if on_symbol is not None:
                on_symbol(name, False)
            return

        if on_symbol is not None:
            on_symbol(name, ok)

    def _run_loop(self, symbol: SymbolState) -> bool:
        produced = False
        for s in symbol.strategies:
            logger.info("Running backtest for %s with strategy %s", symbol.symbol, s.strategy_name)
            s.strategy(backtest=True)
            results = s.strategy.__getattribute__("results")
            if not results:
                logger.warning("No results for %s with strategy %s", symbol.symbol, s.strategy_name)
                continue
            stats = None
            if isinstance(results, dict):
                stats = results.get("15M") or results.get("30M")
                if stats is None and results:
                    stats = next(iter(results.values()))
            if not isinstance(stats, dict):
                continue
            metric = metrics(symbol)
            symbol.score = metric.compute_symbol_score(stats)
            produced = True
        return produced

    def _ensure_symbol_strategy(self, sym: SymbolState) -> None:
        strategy_name = f"{sym.symbol}_EMA"
        if any(getattr(s, "strategy_name", None) == strategy_name for s in sym.strategies):
            return
        strategy = MovingAverageCrossover(sym, self.client, self.cache)
        new_strategy_o = Strategy(strategy_name=strategy_name, strategy=strategy, strategy_score=0.0)
        sym.strategies.append(new_strategy_o)

    def initialise(self) -> None:
        logger.info("Initializing backtest strategies for all symbols.")
        for sym in self.symbol_watch.all_symbols:
            self._ensure_symbol_strategy(sym)
