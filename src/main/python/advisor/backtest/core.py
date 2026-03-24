from advisor.Client.mt5Client import MetaTrader5Client
from advisor.indicators.MA.MovingAverage import MovingAverageCrossover
from advisor.utils.dataHandler import CacheManager
from advisor.backtest.metrics import metrics
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.core.state import Strategy, SymbolState
class Backtest:
    def __init__(self, client: MetaTrader5Client, cache_manager: CacheManager, symbol_watch: SymbolWatch):
        self.client = client
        self.cache = cache_manager
        self.SymbolWatcher = symbol_watch
        self.initialise()

    def run(self, symbol: SymbolState) -> None:
        self.__run_loop(symbol)

    def __run_loop(self, symbol: SymbolState):
        for s in symbol.strategies:
            s.strategy(backtest=True)
            results = s.strategy.__getattribute__("results")
            if not results:
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
            if symbol.score > 0.78:
                symbol.enabled = True

    def initialise(self):
        for sym in self.SymbolWatcher.all_symbols:
            strategy = MovingAverageCrossover(sym, self.client, self.cache)
            new_strategy_o = Strategy(strategy_name=f"{sym.symbol}_EMA", strategy=strategy, strategy_score=0.0)
            sym.strategies.append(new_strategy_o)
