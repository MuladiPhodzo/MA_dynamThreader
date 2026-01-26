WEIGHTS = {
    "win_rate": 0.30,
    "profit_factor": 0.25,
    "expectancy": 0.20,
    "max_drawdown": 0.15,
    "trade_count": 0.10,
}

class metrics:
    def __init__(self):
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
            "trade_count": 140
        }
        """

        score = 0.0

        score += WEIGHTS["win_rate"] * self.normalize(stats["win_rate"], 0.4, 0.8)
        score += WEIGHTS["profit_factor"] * self.normalize(stats["profit_factor"], 1.0, 3.0)
        score += WEIGHTS["expectancy"] * self.normalize(stats["expectancy"], 0.0, 0.01)
        score += WEIGHTS["trade_count"] * self.normalize(stats["trade_count"], 30, 300)

        # Drawdown is inverse (lower is better)
        score += WEIGHTS["max_drawdown"] * (1 - self.normalize(stats["max_drawdown"], 0.05, 0.30))

        return round(score, 4)

    def rank_symbols(self, backtest_results: dict) -> list:
        """
        backtest_results = {
            "EURUSD": {stats},
            "GBPUSD": {stats},
            ...
        }
        """

        scored = []

        for symbol, stats in backtest_results.items():
            score = self.compute_symbol_score(stats)
            scored.append((symbol, score, stats))

        scored.sort(key=lambda x: x[1], reverse=True)

        return [s[0] for s in scored]
