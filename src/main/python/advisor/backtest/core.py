import asyncio
from typing import Callable

from advisor.Client.mt5Client import MetaTrader5Client
from advisor.Strategy_model.indicators.MA.MovingAverage import MovingAverageCrossover
from advisor.utils.dataHandler import CacheManager
from advisor.backtest.metrics import metrics
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.core.state import Strategy, SymbolState, symbolCycle
from advisor.utils.logging_setup import get_logger

logger = get_logger(__name__)
logger.info("Loaded Backtest module from %s", __file__)

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
        self._logged_cache_ready: set[str] = set()
        self._logged_cache_empty: set[str] = set()
        self._logged_strategy_attach: set[str] = set()
        self._logged_strategy_state: set[str] = set()

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

        data = self.cache.get(symbol)
        if not data:
            self._log_cache_empty(symbol)
            return False
        self._log_cache_ready(symbol, data)

        self._log_strategy_state(sym)
        self._ensure_strategy(sym)
        if not getattr(sym, "strategies", None):
            logger.warning("No strategies attached for %s after ensure; skipping run.", sym.symbol)
            return False

        produced = False
        sym.state = symbolCycle.BACKTESTING
        
        for strat in sym.strategies:
            try:
                logger.info(f"[Backtest] Running {symbol} ({strat.strategy_name})")

                await asyncio.to_thread(strat.strategy, True)
                logger.info(f"strategy {strat.strategy_name} finished run")
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
        sym.state = symbolCycle.READY
        return produced

    # -------------------------------------------------
    # Helpers
    # -------------------------------------------------

    def _has_data(self, symbol: str) -> bool:
        data = self.cache.get(symbol)
        return bool(data)

    def _log_cache_ready(self, symbol: str, data) -> None:
        if symbol in self._logged_cache_ready:
            return
        self._logged_cache_ready.add(symbol)
        summary = self._summarize_cache(data)
        logger.info("Backtest data ready for %s: %s", symbol, summary)

    def _log_cache_empty(self, symbol: str) -> None:
        if symbol in self._logged_cache_empty:
            return
        self._logged_cache_empty.add(symbol)
        logger.info("Backtest data missing for %s (cache empty at init)", symbol)

    def _summarize_cache(self, data) -> str:
        if isinstance(data, dict):
            parts = []
            for tf, df in data.items():
                rows = None
                try:
                    rows = len(df) if hasattr(df, "__len__") else None
                except Exception:
                    rows = None
                if rows is None:
                    parts.append(str(tf))
                else:
                    parts.append(f"{tf}:{rows}")
            return "tfs=" + ",".join(parts) if parts else "tfs=none"
        return f"type={type(data).__name__}"

    def _ensure_strategy(self, sym: SymbolState):
        name = f"{sym.symbol}_EMA"
        logger.info("Ensure strategy invoked for %s", sym.symbol)

        if not isinstance(getattr(sym, "strategies", None), list):
            existing = getattr(sym, "strategies", None)
            sym.strategies = list(existing) if existing else []

        if any(s.strategy_name == name for s in sym.strategies):
            logger.info("Strategy already attached for %s (strategies=%d)", sym.symbol, len(sym.strategies))
            return
        sym.state = symbolCycle.INITIALIZING
        logger.info("Attaching strategy for %s (strategies=%d)", sym.symbol, len(sym.strategies))
        try:
            strategy = MovingAverageCrossover(
                sym,
                self.client,
                self.cache,
                start_workers=False,
            )
        except Exception as e:
            logger.exception("Failed to init MovingAverageCrossover for %s: %s", sym.symbol, e)
            return

        sym.strategies.append(
            Strategy(strategy_name=name, strategy=strategy, strategy_score=0.0)
        )
        if sym.symbol not in self._logged_strategy_attach:
            self._logged_strategy_attach.add(sym.symbol)
            logger.info("Attached MovingAverageCrossover for %s (strategies=%d)", sym.symbol, len(sym.strategies))

    def _log_strategy_state(self, sym: SymbolState) -> None:
        if sym.symbol in self._logged_strategy_state:
            return
        self._logged_strategy_state.add(sym.symbol)
        logger.info(
            "Backtest symbol state for %s: strategies=%d type=%s",
            sym.symbol,
            len(sym.strategies) if hasattr(sym, "strategies") and sym.strategies is not None else -1,
            type(getattr(sym, "strategies", None)).__name__,
        )

    def _extract_stats(self, results: dict):
        stats = results.get("15M") or results.get("30M")
        if stats is None and results:
            stats = next(iter(results.values()))
        return stats
