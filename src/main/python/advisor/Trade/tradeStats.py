import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from advisor.utils.cache_handler import CacheManager as Cache
from advisor.utils.logging_setup import get_logger
logger = get_logger(__name__)
class TradeStats:
    def __init__(self, data_path="stats/trading_stats.csv", reports_path="stats/reports"):
        self.data_path = data_path
        self.reports_path = reports_path
        os.makedirs(os.path.dirname(data_path), exist_ok=True)
        os.makedirs(reports_path, exist_ok=True)
        self.df = self._load_data()
        self.cache = Cache()

        self.num_trades = len(self.df)
        self.tradesInProfit = 0
        self.tradesInLoss = 0
        self.accountChangePercent = self.growth_rate()
        self.profit = self.total_profit
        self.loss = self.total_loss()
        self.drawdown = self.max_drawdown()
        self.sharpeRatio = self.sharpe_ratio()
        self.sortinoRatio = self.sortino_ratio()
        self.avgProfit = self.avg_profit()
        self.winRate = self.win_rate()

    # -------------------------------------------------------------------------
    # Data Persistence
    # -------------------------------------------------------------------------
    def _load_data(self):
        if os.path.exists(self.data_path):
            df = pd.read_csv(self.data_path, parse_dates=["timestamp"])
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
            return df
        else:
            return pd.DataFrame(columns=[
                "timestamp", "symbol", "profit", "balance_before",
                "balance_after", "lot_size", "duration_sec"
            ])

    def _save(self):
        self.df.to_csv(self.data_path, index=False)

    # -------------------------------------------------------------------------
    # Data Logging
    # -------------------------------------------------------------------------
    def log_trade(self, symbol, profit, balance_before, balance_after, lot_size, duration):
        new_trade = pd.DataFrame([{
            "timestamp": datetime.now(timezone.utc),
            "symbol": symbol,
            "profit": profit,
            "balance_before": balance_before,
            "balance_after": balance_after,
            "lot_size": lot_size,
            "duration_sec": duration
        }])
        self.df = pd.concat([self.df, new_trade], ignore_index=True)
        self._save()

    def updateTrade(self, profit, balance_after):
        if len(self.df) == 0:
            logger.warning("No trades to update.")
            return
        self.df.at[len(self.df) - 1, "profit"] = profit
        self.df.at[len(self.df) - 1, "balance_after"] = balance_after
        self._save()

    # -------------------------------------------------------------------------
    # Core Metrics
    # -------------------------------------------------------------------------
    def _returns(self):
        if "balance_before" not in self.df or len(self.df) == 0:
            return np.array([])
        return self.df["profit"] / self.df["balance_before"]

    def total_profit(self):
        return self.df["profit"].sum()

    def total_loss(self):
        return self.df["profit"].sum()

    def avg_profit(self):
        return self.df["profit"].mean() if len(self.df) > 0 else 0.0

    def win_rate(self):
        total = len(self.df)
        if total == 0:
            return 0.0
        wins = (self.df["profit"] > 0).sum()
        return 100 * wins / total

    def profit_factor(self):
        gross_profit = self.df.loc[self.df["profit"] > 0, "profit"].sum()
        gross_loss = abs(self.df.loc[self.df["profit"] < 0, "profit"].sum())
        return (gross_profit / gross_loss) if gross_loss > 0 else np.inf

    def max_drawdown(self):
        if "balance_after" not in self.df or len(self.df) == 0:
            return 0.0
        balance = self.df["balance_after"].to_numpy()
        roll_max = np.maximum.accumulate(balance)
        drawdown = (roll_max - balance) / roll_max
        return np.max(drawdown) * 100  # percent

    def volatility(self):
        r = self._returns()
        return np.std(r) * np.sqrt(252) if len(r) > 1 else 0.0  # annualized

    def sharpe_ratio(self, risk_free_rate=0.01):
        r = self._returns()
        if len(r) < 2:
            return 0.0
        mean_ret = np.mean(r) - (risk_free_rate / 252)
        std_ret = np.std(r)
        return 0.0 if std_ret == 0 else (mean_ret / std_ret) * np.sqrt(252)

    def sortino_ratio(self, risk_free_rate=0.01):
        r = self._returns()
        if len(r) < 2:
            return 0.0
        downside = r[r < 0]
        downside_std = np.std(downside) if len(downside) > 0 else 0
        mean_ret = np.mean(r) - (risk_free_rate / 252)
        return 0.0 if downside_std == 0 else (mean_ret / downside_std) * np.sqrt(252)

    def value_at_risk(self, alpha=0.05):
        r = self._returns()
        if len(r) == 0:
            return 0.0
        return np.percentile(r, 100 * alpha)

    def growth_rate(self):
        if len(self.df) < 2:
            return 0.0
        start, end = self.df.iloc[0]["balance_before"], self.df.iloc[-1]["balance_after"]
        return ((end - start) / start) * 100 if start > 0 else 0.0

    # -------------------------------------------------------------------------
    # Summaries
    # -------------------------------------------------------------------------
    def summary(self):
        return {
            "total_trades": len(self.df),
            "total_profit": round(self.total_profit(), 2),
            "avg_profit": round(self.avg_profit(), 2),
            "win_rate_%": round(self.win_rate(), 2),
            "profit_factor": round(self.profit_factor(), 2),
            "max_drawdown_%": round(self.max_drawdown(), 2),
            "sharpe_ratio": round(self.sharpe_ratio(), 3),
            "sortino_ratio": round(self.sortino_ratio(), 3),
            "volatility_%": round(self.volatility() * 100, 2),
            "VaR_5%": round(self.value_at_risk(), 4),
            "growth_%": round(self.growth_rate(), 2)
        }

    def summary_by_symbol(self):
        if self.df.empty:
            return pd.DataFrame()
        grouped = self.df.groupby("symbol")["profit"].agg(["count", "sum", "mean"])
        grouped.rename(columns={"count": "trades", "sum": "total_profit", "mean": "avg_profit"}, inplace=True)
        return grouped.sort_values("total_profit", ascending=False)

    # -------------------------------------------------------------------------
    # Auto Reports (Daily / Weekly)
    # -------------------------------------------------------------------------
    def generate_report(self, period="daily"):
        if self.df.empty:
            logger.info("⚠️ No data for report generation.")
            return None

        now = datetime.now(timezone.utc)
        if period == "daily":
            cutoff = now - timedelta(days=1)
            report_name = f"daily_report_{now.strftime('%Y_%m_%d')}.csv"
        elif period == "weekly":
            cutoff = now - timedelta(days=7)
            report_name = f"weekly_report_{now.strftime('%Y_%m_%d')}.csv"
        else:
            raise ValueError("period must be 'daily' or 'weekly'")

        df_period = self.df[self.df["timestamp"] >= cutoff]
        if df_period.empty:
            logger.info(f"⚠️ No trades for {period} report.")
            return None

        # Compute performance metrics for the period
        stats = {
            "period": period,
            "start_date": df_period["timestamp"].min(),
            "end_date": df_period["timestamp"].max(),
            "trades": len(df_period),
            "total_profit": df_period["profit"].sum(),
            "avg_profit": df_period["profit"].mean(),
            "win_rate_%": 100 * (df_period["profit"] > 0).sum() / len(df_period),
            "max_drawdown_%": round(self.max_drawdown(), 2),
            "sharpe_ratio": round(self.sharpe_ratio(), 3),
            "sortino_ratio": round(self.sortino_ratio(), 3),
            "growth_%": round(self.growth_rate(), 2)
        }

        # Save CSV report
        report_path = os.path.join(self.reports_path, report_name)
        df_period.to_csv(report_path, index=False)

        logger.info(f"📊 {period.capitalize()} report saved to: {report_path}")
        return stats
