import pandas as pd
class TradeStats:
    def __init__(self, trade_data: pd.DataFrame = None):
        self.total_trades = 0
        self.successful_trades = 0
        self.failed_trades = 0
        self.total_profit = 0.0
        self.total_loss = 0.0

        if trade_data is not None:
            self.load_trade_data(trade_data)

    def load_trade_data(self, trade_data):
        for trade in trade_data:
            self.record_trade(trade["profit_loss"], 0)

    def record_trade(self, profit_loss, duration):
        self.total_trades += 1
        if profit_loss > 0:
            self.successful_trades += 1
            self.total_profit += profit_loss
        else:
            self.failed_trades += 1
            self.total_loss += abs(profit_loss)
            
    def get_summary(self):
        return {
            "Total Trades": self.total_trades,
            "Successful Trades": self.successful_trades,
            "Failed Trades": self.failed_trades,
            "Total Profit": self.total_profit,
            "Total Loss": self.total_loss,
            "Success Rate": (self.successful_trades / self.total_trades * 100) if self.total_trades > 0 else 0.0
        }
        
    def calculate_average_profit(self, avg_trade: int, period: int):
        if self.total_trades == 0:
            return 0.0
        else:
            return (self.total_profit - self.total_loss) / self.total_trades

    def calculate_average_weekly_profit(self):
        return self.calculate_average_profit(10, 7)
    
    def calculate_average_monthly_profit(self):
        return self.calculate_average_profit(40, 30)
    
    def calculate_max_drawDown(self):
        # Placeholder for max drawdown calculation
        return 0.0
    
    def calculate_sharpe_ratio(self, risk_free_rate=0.01):
        # Placeholder for Sharpe ratio calculation
        return 0.0
    
    def calculate_sortino_ratio(self, risk_free_rate=0.01):
        # Placeholder for Sortino ratio calculation
        return 0.0
    
    def calculate_profit_factor(self):
        if self.total_loss == 0:
            return float('inf')  # Infinite profit factor if no losses
        return self.total_profit / self.total_loss