from advisor.Client.mt5Client import MetaTrader5Client
from advisor.indicators.MA.MovingAverage import MovingAverageCrossover
from advisor.utils.dataHandler import CacheManager
from advisor.backtest.metrics import metrics
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.core.state import Strategy
class Backtest:
    def __init__(self, client: MetaTrader5Client, cache_manager: CacheManager, symbol_watch: SymbolWatch):
        self.client = client
        self.cache = cache_manager
        self.SymbolWatcher = symbol_watch
        self.initialise()

    def __run_loop(self):
        for sym in self.SymbolWatcher.all_symbols:
            for s in sym.strategies:
                s.strategy()
                results = s.strategy.__getattribute__("results")
                metric = metrics(sym)
                sym.score = metric.compute_symbol_score(results)
                if sym.score > 0.78:
                    sym.enabled = True

    def initialise(self):
        for sym in self.SymbolWatcher.all_symbols:
            strategy = MovingAverageCrossover(sym, self.client)
            new_strategy_o = Strategy(strategy_name=f"{sym.symbol}_EMA", strategy=strategy, strategy_score=0.0)
            sym.strategies.append(new_strategy_o)

