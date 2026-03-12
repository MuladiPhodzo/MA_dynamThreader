from core.state import SymbolState


WEIGHTS = {
    "win_rate": 0.30,
    "profit_factor": 0.25,
    "expectancy": 0.20,
    "max_drawdown": 0.15,
    "total_trades": 0.10,
}

class metrics:
    def __init__(self, symbol_state: SymbolState):
        self.symbol = symbol_state
        pass

    def clamp(value, min_v=0.0, max_v=1.0):
        return max(min_v, min(value, max_v))

    def normalize(self, value, min_v, max_v):
        if max_v == min_v:
            return 0.0
        return self.clamp((value - min_v) / (max_v - min_v))

    def compute_symbol_score(self, stats: dict) -> float:
        """
        stats example:
        {
            "win_rate": 0.62,
            "profit_factor": 1.8,
            "expectancy": 0.004,
            "max_drawdown": 0.12,
            "total_trades": 140
        }
        """

        self.symbol.score += WEIGHTS["win_rate"] * self.normalize(stats["win_rate"], 0.4, 0.8)
        self.symbol.score += WEIGHTS["profit_factor"] * self.normalize(stats["profit_factor"], 1.0, 3.0)
        self.symbol.score += WEIGHTS["expectancy"] * self.normalize(stats["expectancy"], 0.0, 0.01)
        self.symbol.score += WEIGHTS["total_trades"] * self.normalize(stats["total_trades"], 30, 300)

        # Drawdown is inverse (lower is better)
        self.symbol.score += WEIGHTS["max_drawdown"] * (1 - self.normalize(stats["max_drawdown"], 0.05, 0.30))

        self.symbol.score = round(self.symbol.score, 4)
