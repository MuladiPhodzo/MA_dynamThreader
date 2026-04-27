from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import pandas as pd

from advisor.Client.mt5Client import MetaTrader5Client
from advisor.Strategy_model.strategy import Strategy as StrategyModel
from advisor.utils.dataHandler import CacheManager
from advisor.backtest.metrics import metrics
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.core.state import Strategy, SymbolState, symbolCycle
from advisor.utils.logging_setup import get_logger

logger = get_logger(__name__)
logger.info("Loaded Backtest core module from %s", __file__)


@dataclass
class SimulatedTrade:
    symbol: str
    direction: Literal["Buy", "Sell"]
    entry_time: Any
    exit_time: Any
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    volume: float
    pnl: float
    pnl_pct: float
    exit_reason: str


@dataclass
class SimulationResult:
    symbol: str
    initial_balance: float
    final_balance: float
    net_profit: float
    return_pct: float
    max_drawdown: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    expectancy: float
    total_trades: int
    wins: int
    losses: int
    trades: list[SimulatedTrade] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)

    def stats(self) -> dict[str, Any]:
        return {
            "initial_balance": self.initial_balance,
            "final_balance": self.final_balance,
            "net_profit": self.net_profit,
            "return_pct": self.return_pct,
            "max_drawdown": self.max_drawdown_pct,
            "max_drawdown_abs": self.max_drawdown,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "expectancy": self.expectancy,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
        }


@dataclass
class BacktestResult:
    symbol: str
    strategy_name: str
    ok: bool
    passed: bool = False
    score: float = 0.0
    confidence: float = 0.0
    reason: str = ""
    stats: dict[str, Any] = field(default_factory=dict)
    simulation: dict[str, Any] = field(default_factory=dict)
    trades: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BacktestBatchResult:
    strategy_name: str
    requested_symbols: int
    tested_symbols: int
    passed_symbols: int
    seeded_symbols: int
    results: list[BacktestResult]

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "requested_symbols": self.requested_symbols,
            "tested_symbols": self.tested_symbols,
            "passed_symbols": self.passed_symbols,
            "seeded_symbols": self.seeded_symbols,
            "results": [r.__dict__ for r in self.results],
        }


class TradingSimulator:
    """
    Lightweight OHLC trade simulator.

    Entry:
        Next candle open after a row with a valid signal.

    Direction:
        Signal/Direction column, otherwise signed Score.

    Exit:
        Stop_Loss/Take_Profit columns if present.
        ATR-based SL/TP if ATR exists.
        Fallback fixed percentage distance if neither exists.

    Conservative assumption:
        If SL and TP are both touched in one candle, SL is assumed first.
    """

    def __init__(
        self,
        initial_balance: float = 10_000.0,
        risk_per_trade: float = 0.01,
        spread_cost: float = 0.0,
        max_bars_in_trade: int = 100,
        allow_multiple_positions: bool = False,
    ) -> None:
        self.initial_balance = float(initial_balance)
        self.risk_per_trade = float(risk_per_trade)
        self.spread_cost = float(spread_cost)
        self.max_bars_in_trade = int(max_bars_in_trade)
        self.allow_multiple_positions = bool(allow_multiple_positions)

    def simulate(self, symbol: str, entry_df: pd.DataFrame) -> SimulationResult:
        if entry_df is None or entry_df.empty or len(entry_df) < 3:
            return self._empty_result(symbol)

        df = self._prepare_frame(entry_df)
        balance = self.initial_balance
        peak = balance
        max_drawdown = 0.0
        open_until_idx = -1
        trades: list[SimulatedTrade] = []
        equity_curve: list[dict[str, Any]] = []

        for i in range(0, len(df) - 2):
            if not self.allow_multiple_positions and i <= open_until_idx:
                equity_curve.append({"time": self._row_time(df, i), "equity": round(balance, 6)})
                continue

            row = df.iloc[i]
            direction = self._signal_direction(row)
            if direction is None:
                equity_curve.append({"time": self._row_time(df, i), "equity": round(balance, 6)})
                continue

            entry_idx = i + 1
            entry_row = df.iloc[entry_idx]
            entry_price = self._safe_float(entry_row.get("open", entry_row.get("close")), 0.0)
            if entry_price <= 0:
                continue

            stop_loss, take_profit = self._resolve_sl_tp(direction, entry_price, row)
            volume = self._position_size(balance, entry_price, stop_loss)
            if volume <= 0:
                continue

            exit_idx, exit_price, exit_reason = self._find_exit(
                df=df,
                start_idx=entry_idx,
                direction=direction,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

            pnl = self._calculate_pnl(direction, entry_price, exit_price, volume) - self.spread_cost
            balance += pnl
            peak = max(peak, balance)
            max_drawdown = max(max_drawdown, peak - balance)

            trade = SimulatedTrade(
                symbol=symbol,
                direction=direction,
                entry_time=self._row_time(df, entry_idx),
                exit_time=self._row_time(df, exit_idx),
                entry_price=round(entry_price, 6),
                exit_price=round(exit_price, 6),
                stop_loss=round(stop_loss, 6),
                take_profit=round(take_profit, 6),
                volume=round(volume, 6),
                pnl=round(pnl, 6),
                pnl_pct=round((pnl / self.initial_balance) * 100.0, 6),
                exit_reason=exit_reason,
            )
            trades.append(trade)
            open_until_idx = exit_idx
            equity_curve.append({"time": trade.exit_time, "equity": round(balance, 6)})

        return self._build_result(symbol, balance, max_drawdown, trades, equity_curve)

    def _prepare_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        if not isinstance(out.index, pd.DatetimeIndex):
            for col in ("time", "datetime", "timestamp"):
                if col in out.columns:
                    out.index = pd.to_datetime(out[col], errors="coerce")
                    break
        return out.sort_index()

    def _signal_direction(self, row: pd.Series) -> Literal["Buy", "Sell"] | None:
        if "Filtered" in row.index and not bool(row.get("Filtered", False)):
            return None

        score = self._safe_float(row.get("Score", 0.0), 0.0)
        signal = str(row.get("Signal", "")).lower()
        direction = str(row.get("Direction", "")).lower()

        if signal in {"buy", "bullish", "long"} or direction in {"buy", "bullish", "long"} or score > 0:
            return "Buy"
        if signal in {"sell", "bearish", "short"} or direction in {"sell", "bearish", "short"} or score < 0:
            return "Sell"
        return None

    def _resolve_sl_tp(self, direction: Literal["Buy", "Sell"], entry: float, row: pd.Series) -> tuple[float, float]:
        explicit_sl = self._safe_float(row.get("Stop_Loss", row.get("SL", 0.0)), 0.0)
        explicit_tp = self._safe_float(row.get("Take_Profit", row.get("TP", 0.0)), 0.0)
        if explicit_sl > 0 and explicit_tp > 0:
            return explicit_sl, explicit_tp

        atr = self._safe_float(row.get("ATR", 0.0), 0.0)
        sl_mult = self._safe_float(row.get("sl_atr_mult", 1.5), 1.5)
        tp_mult = self._safe_float(row.get("tp_atr_mult", 3.0), 3.0)

        risk_distance = atr * sl_mult if atr > 0 else entry * 0.002
        reward_distance = atr * tp_mult if atr > 0 else risk_distance * 2.0

        if direction == "Buy":
            return entry - risk_distance, entry + reward_distance
        return entry + risk_distance, entry - reward_distance

    def _position_size(self, balance: float, entry: float, stop_loss: float) -> float:
        risk_amount = balance * self.risk_per_trade
        risk_per_unit = abs(entry - stop_loss)
        return risk_amount / risk_per_unit if risk_per_unit > 0 else 0.0

    def _find_exit(
        self,
        df: pd.DataFrame,
        start_idx: int,
        direction: Literal["Buy", "Sell"],
        stop_loss: float,
        take_profit: float,
    ) -> tuple[int, float, str]:
        end_idx = min(start_idx + self.max_bars_in_trade, len(df) - 1)

        for idx in range(start_idx, end_idx + 1):
            row = df.iloc[idx]
            high = self._safe_float(row.get("high", row.get("close")), 0.0)
            low = self._safe_float(row.get("low", row.get("close")), 0.0)
            close = self._safe_float(row.get("close"), 0.0)

            if direction == "Buy":
                hit_sl = low <= stop_loss
                hit_tp = high >= take_profit
            else:
                hit_sl = high >= stop_loss
                hit_tp = low <= take_profit

            if hit_sl and hit_tp:
                return idx, stop_loss, "SL_FIRST_ASSUMPTION"
            if hit_sl:
                return idx, stop_loss, "SL"
            if hit_tp:
                return idx, take_profit, "TP"
            if idx == end_idx:
                return idx, close, "TIME_EXIT"

        fallback = self._safe_float(df.iloc[end_idx].get("close"), 0.0)
        return end_idx, fallback, "TIME_EXIT"

    @staticmethod
    def _calculate_pnl(direction: Literal["Buy", "Sell"], entry: float, exit_price: float, volume: float) -> float:
        return (exit_price - entry) * volume if direction == "Buy" else (entry - exit_price) * volume

    def _build_result(
        self,
        symbol: str,
        final_balance: float,
        max_drawdown: float,
        trades: list[SimulatedTrade],
        equity_curve: list[dict[str, Any]],
    ) -> SimulationResult:
        wins = [trade for trade in trades if trade.pnl > 0]
        losses = [trade for trade in trades if trade.pnl <= 0]
        gross_profit = sum(trade.pnl for trade in wins)
        gross_loss = abs(sum(trade.pnl for trade in losses))
        total_trades = len(trades)
        net_profit = final_balance - self.initial_balance

        return SimulationResult(
            symbol=symbol,
            initial_balance=self.initial_balance,
            final_balance=round(final_balance, 6),
            net_profit=round(net_profit, 6),
            return_pct=round((net_profit / self.initial_balance) * 100.0, 6),
            max_drawdown=round(max_drawdown, 6),
            max_drawdown_pct=round(max_drawdown / max(self.initial_balance, 1e-9), 6),
            win_rate=round(len(wins) / total_trades, 6) if total_trades else 0.0,
            profit_factor=round(gross_profit / gross_loss, 6) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0,
            expectancy=round(net_profit / total_trades, 6) if total_trades else 0.0,
            total_trades=total_trades,
            wins=len(wins),
            losses=len(losses),
            trades=trades,
            equity_curve=equity_curve,
        )

    def _empty_result(self, symbol: str) -> SimulationResult:
        return SimulationResult(
            symbol=symbol,
            initial_balance=self.initial_balance,
            final_balance=self.initial_balance,
            net_profit=0.0,
            return_pct=0.0,
            max_drawdown=0.0,
            max_drawdown_pct=0.0,
            win_rate=0.0,
            profit_factor=0.0,
            expectancy=0.0,
            total_trades=0,
            wins=0,
            losses=0,
        )

    @staticmethod
    def _row_time(df: pd.DataFrame, idx: int) -> Any:
        value = df.index[idx]
        return value.isoformat() if isinstance(value, pd.Timestamp) else value

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None or pd.isna(value):
                return default
            return float(value)
        except Exception:
            return default


class Backtest:
    """
    Pure backtest core.

    Responsibilities:
    - seed/request >1yr data
    - seed StrategyModel
    - run strategy
    - simulate entries
    - score result
    - return structured result to backtest_runner.py

    It intentionally does not know about EventBus, HealthBus, Scheduler, or BotLifecycle.
    """

    DEFAULT_TIMEFRAMES = ("15M", "1H", "4H", "1D")
    DEFAULT_RESULT_TIMEFRAMES = ("15M", "30M", "5M", "1H", "4H", "1D")

    def __init__(
        self,
        client: MetaTrader5Client,
        cache: CacheManager,
        symbol_watch: SymbolWatch,
        pipeline: Any | None = None,
        initial_balance: float = 10_000.0,
        risk_per_trade: float = 0.01,
        min_score: float = 0.65,
        min_confidence: float = 50.0,
    ) -> None:
        self.client = client
        self.cache = cache
        self.symbol_watch = symbol_watch
        self.pipeline = pipeline
        self.initial_balance = initial_balance
        self.risk_per_trade = risk_per_trade
        self.min_score = min_score
        self.min_confidence = min_confidence

    async def run_symbol(
        self,
        strategy_name: str,
        symbol: str,
        *,
        years: int = 1,
        simulate: bool = True,
        spread_cost: float = 0.0,
        max_bars_in_trade: int = 100,
    ) -> BacktestResult:
        sym = self.symbol_watch.get(symbol)
        if sym is None:
            return BacktestResult(symbol=symbol, strategy_name=strategy_name, ok=False, reason="symbol_not_found")

        previous_state = getattr(sym, "state", None)
        sym.state = symbolCycle.BACKTESTING

        try:
            data = await self._load_backtest_data(symbol, years=years)
            if not self._has_usable_data(data):
                return BacktestResult(symbol=symbol, strategy_name=strategy_name, ok=False, reason="no_backtest_data")

            wrapper = self._seed_strategy(sym, strategy_name)
            if wrapper is None:
                return BacktestResult(symbol=symbol, strategy_name=strategy_name, ok=False, reason="strategy_seed_failed")

            self._seed_data(wrapper.strategy, data)

            raw_results = await asyncio.to_thread(self._execute_strategy, wrapper.strategy)
            entry_frame = self._extract_entry_frame(raw_results)
            if entry_frame is None or entry_frame.empty:
                return BacktestResult(symbol=symbol, strategy_name=strategy_name, ok=False, reason="no_strategy_output")

            simulation_stats: dict[str, Any] = {}
            trade_rows: list[dict[str, Any]] = []
            score_input: Any = entry_frame

            if simulate:
                simulator = TradingSimulator(
                    initial_balance=self.initial_balance,
                    risk_per_trade=self.risk_per_trade,
                    spread_cost=spread_cost,
                    max_bars_in_trade=max_bars_in_trade,
                )
                simulation = simulator.simulate(symbol, entry_frame)
                simulation_stats = simulation.stats()
                trade_rows = [trade.__dict__ for trade in simulation.trades]
                score_input = simulation_stats

            score, confidence = self._score_symbol(sym, score_input)
            passed = self._meets_requirements(score, confidence)

            wrapper.strategy_score = score
            self._update_symbol_after_backtest(sym, strategy_name, score, confidence, passed)

            return BacktestResult(
                symbol=symbol,
                strategy_name=strategy_name,
                ok=True,
                passed=passed,
                score=score,
                confidence=confidence,
                reason="passed" if passed else "requirements_not_met",
                stats=self._compact_stats(score_input),
                simulation=simulation_stats,
                trades=trade_rows,
            )

        except Exception as exc:
            logger.exception("%s:%s backtest failed", strategy_name, symbol)
            return BacktestResult(symbol=symbol, strategy_name=strategy_name, ok=False, reason=f"failed:{exc}")

        finally:
            sym.state = previous_state or symbolCycle.READY

    async def run_many(
        self,
        strategy_name: str,
        symbols: list[str],
        *,
        years: int = 1,
        simulate: bool = True,
    ) -> BacktestBatchResult:
        results = []
        for symbol in symbols:
            results.append(
                await self.run_symbol(
                    strategy_name=strategy_name,
                    symbol=symbol,
                    years=years,
                    simulate=simulate,
                )
            )

        return BacktestBatchResult(
            strategy_name=strategy_name,
            requested_symbols=len(symbols),
            tested_symbols=sum(1 for result in results if result.ok),
            passed_symbols=sum(1 for result in results if result.passed),
            seeded_symbols=sum(1 for result in results if result.passed),
            results=results,
        )

    async def run_top_symbols(
        self,
        strategy_name: str,
        *,
        top_n: int = 20,
        years: int = 1,
        simulate: bool = True,
    ) -> BacktestBatchResult:
        symbols = [sym.symbol for sym in self.pick_top_symbols(top_n)]
        return await self.run_many(strategy_name, symbols, years=years, simulate=simulate)

    def pick_top_symbols(self, limit: int = 20) -> list[SymbolState]:
        symbols = self._iter_symbol_states()

        def rank(sym: SymbolState) -> tuple[int, float, int, str]:
            meta = getattr(sym, "meta", {}) or {}
            desired_enabled = bool(meta.get("desired_enabled", False))
            enabled = bool(getattr(sym, "enabled", False))
            score = self._safe_float(getattr(sym, "score", 0.0), 0.0)
            activity = int(meta.get("Total_trades", 0) or 0) + int(meta.get("Total_signals", 0) or 0)
            return (1 if enabled or desired_enabled else 0, score, activity, str(getattr(sym, "symbol", "")))

        return sorted(symbols, key=rank, reverse=True)[: max(int(limit), 0)]

    def seed_successful_strategy_to_rest(
        self,
        strategy_name: str,
        successful_symbols: list[str],
        *,
        min_score: float | None = None,
    ) -> int:
        """
        After top-20 backtest, seed the successful strategy into the remaining symbols.
        This does not enable all remaining symbols. It only attaches the strategy so live
        strategy/execution layers can evaluate them later.
        """
        if not successful_symbols:
            return 0

        min_score = self.min_score if min_score is None else min_score
        seeded = 0

        for sym in self._iter_symbol_states():
            if sym.symbol in successful_symbols:
                continue

            best_score = max(
                [
                    self._safe_float(getattr(self.symbol_watch.get(s), "score", 0.0), 0.0)
                    for s in successful_symbols
                    if self.symbol_watch.get(s) is not None
                ],
                default=0.0,
            )
            if best_score < min_score:
                continue

            if self._seed_strategy(sym, strategy_name) is not None:
                seeded += 1

        return seeded

    async def _load_backtest_data(self, symbol: str, *, years: int = 1) -> Any:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=max(int(years), 1) * 365 + 10)

        if self.pipeline is not None:
            for method_name in ("request_history", "load_history", "fetch_history", "ensure_history"):
                method = getattr(self.pipeline, method_name, None)
                if not callable(method):
                    continue

                try:
                    result = method(
                        symbol=symbol,
                        timeframes=self.DEFAULT_TIMEFRAMES,
                        start=start,
                        end=end,
                    )
                    if asyncio.iscoroutine(result):
                        result = await result
                    if self._has_usable_data(result):
                        self._write_cache(symbol, result)
                        return result
                except TypeError:
                    try:
                        result = method(symbol, self.DEFAULT_TIMEFRAMES, start, end)
                        if asyncio.iscoroutine(result):
                            result = await result
                        if self._has_usable_data(result):
                            self._write_cache(symbol, result)
                            return result
                    except Exception:
                        logger.exception("Pipeline %s failed for %s", method_name, symbol)
                except Exception:
                    logger.exception("Pipeline %s failed for %s", method_name, symbol)

        return self.cache.get(symbol)

    def _seed_strategy(self, sym: SymbolState, strategy_name: str) -> Strategy | None:
        if not isinstance(getattr(sym, "strategies", None), list):
            existing = getattr(sym, "strategies", None)
            sym.strategies = list(existing) if existing else []

        for wrapper in sym.strategies:
            if getattr(wrapper, "strategy_name", None) == strategy_name:
                return wrapper

        try:
            strategy_obj = StrategyModel(symbol=sym.symbol, cacheManager=self.cache)
        except TypeError:
            # Compatibility fallback for older StrategyModel constructor variants.
            strategy_obj = StrategyModel(symbol=sym.symbol, data=self.cache.get(sym.symbol))
        except Exception:
            logger.exception("Failed to create StrategyModel %s for %s", strategy_name, sym.symbol)
            return None

        wrapper = Strategy(strategy_name=strategy_name, strategy=strategy_obj, strategy_score=0.0)
        sym.strategies.append(wrapper)
        return wrapper

    @staticmethod
    def _seed_data(strategy_obj: Any, data: dict[str, pd.DataFrame] | pd.DataFrame) -> None:
        cloned = {tf: df.copy() for tf, df in data.items() if isinstance(df, pd.DataFrame)} if isinstance(data, dict) else {}

        if hasattr(strategy_obj, "data_handler") and hasattr(strategy_obj.data_handler, "data"):
            strategy_obj.data_handler.data = cloned

        if hasattr(strategy_obj, "data"):
            strategy_obj.data = cloned

    @staticmethod
    def _execute_strategy(strategy_obj: Any) -> Any:
        if hasattr(strategy_obj, "run") and callable(strategy_obj.run):
            result = strategy_obj.run(backtest=True)
        elif hasattr(strategy_obj, "strategy") and callable(strategy_obj.strategy):
            result = strategy_obj.strategy(True)
        elif callable(strategy_obj):
            result = strategy_obj(True)
        else:
            raise TypeError(f"Unsupported strategy object: {type(strategy_obj).__name__}")

        return result if result is not None else getattr(strategy_obj, "results", None)

    def _extract_entry_frame(self, results: Any) -> pd.DataFrame | None:
        if isinstance(results, pd.DataFrame):
            return results
        if isinstance(results, dict):
            for tf in self.DEFAULT_RESULT_TIMEFRAMES:
                value = results.get(tf)
                if isinstance(value, pd.DataFrame) and not value.empty:
                    return value
            for value in results.values():
                if isinstance(value, pd.DataFrame) and not value.empty:
                    return value
        return None

    def _score_symbol(self, sym: SymbolState, stats: Any) -> tuple[float, float]:
        if getattr(sym, "score", None) is None:
            sym.score = 0.0

        if isinstance(stats, dict):
            score = self._simulation_score(stats)
            confidence = self._confidence_from_stats(stats, score)
        else:
            metric = metrics(sym)
            score = self._safe_float(metric.compute_symbol_score(stats), 0.0)
            confidence = self._confidence_from_stats(stats, score)

        sym.score = score
        return score, confidence

    def _simulation_score(self, stats: dict[str, Any]) -> float:
        win_rate = self._safe_float(stats.get("win_rate"), 0.0)
        profit_factor = self._safe_float(stats.get("profit_factor"), 0.0)
        expectancy = self._safe_float(stats.get("expectancy"), 0.0)
        max_drawdown = self._safe_float(stats.get("max_drawdown"), 0.0)
        total_trades = self._safe_float(stats.get("total_trades"), 0.0)

        score = 0.0
        score += 0.30 * self._normalize(win_rate, 0.4, 0.8)
        score += 0.25 * self._normalize(profit_factor, 1.0, 3.0)
        score += 0.20 * self._normalize(expectancy, 0.0, 0.01)
        score += 0.10 * self._normalize(total_trades, 30, 300)
        score += 0.15 * (1 - self._normalize(max_drawdown, 0.05, 0.30))
        return round(max(0.0, min(score, 1.0)), 4)

    def _confidence_from_stats(self, stats: Any, score: float) -> float:
        if isinstance(stats, dict):
            for key in ("Confidence", "confidence", "win_rate", "Win_Rate"):
                if key in stats:
                    value = self._safe_float(stats.get(key), 0.0)
                    return value * 100 if 0 <= value <= 1 else value

        if isinstance(stats, pd.DataFrame) and not stats.empty:
            latest = stats.iloc[-1]
            for key in ("Confidence", "confidence", "win_rate", "Win_Rate"):
                if key in latest.index:
                    value = self._safe_float(latest.get(key), 0.0)
                    return value * 100 if 0 <= value <= 1 else value

        return min(abs(score) * 100, 100.0)

    def _meets_requirements(self, score: float, confidence: float) -> bool:
        return score >= self.min_score and confidence >= self.min_confidence

    def _update_symbol_after_backtest(
        self,
        sym: SymbolState,
        strategy_name: str,
        score: float,
        confidence: float,
        passed: bool,
    ) -> None:
        if not isinstance(getattr(sym, "meta", None), dict):
            sym.meta = {}

        now = datetime.now(timezone.utc).isoformat()
        sym.last_backtest = now
        sym.score = score
        sym.meta["last_backtest_at"] = now
        sym.meta["last_backtest_strategy"] = strategy_name
        sym.meta["last_backtest_score"] = score
        sym.meta["last_backtest_confidence"] = confidence
        sym.meta["backtest_passed"] = passed
        sym.meta["desired_enabled"] = passed

        sym.enabled = passed
        sym.state = symbolCycle.READY if passed else symbolCycle.DEGRADED

    def _write_cache(self, symbol: str, data: Any) -> None:
        for method_name in ("set", "put", "update", "save"):
            method = getattr(self.cache, method_name, None)
            if callable(method):
                try:
                    method(symbol, data)
                    return
                except Exception:
                    logger.exception("Cache write via %s failed for %s", method_name, symbol)
                    return

    def _iter_symbol_states(self) -> list[SymbolState]:
        for attr in ("symbols", "items", "watch", "_symbols"):
            value = getattr(self.symbol_watch, attr, None)
            if isinstance(value, dict):
                return [sym for sym in value.values() if sym is not None]
            if isinstance(value, list):
                return [sym for sym in value if sym is not None]

        if hasattr(self.symbol_watch, "all") and callable(self.symbol_watch.all):
            return list(self.symbol_watch.all() or [])

        if hasattr(self.symbol_watch, "values") and callable(self.symbol_watch.values):
            return list(self.symbol_watch.values())

        return []

    @staticmethod
    def _compact_stats(stats: Any) -> dict[str, Any]:
        if isinstance(stats, dict):
            return dict(stats)
        if isinstance(stats, pd.DataFrame):
            if stats.empty:
                return {"rows": 0}
            latest = stats.iloc[-1].to_dict()
            keep = {
                "Score",
                "Confidence",
                "Legacy_Score",
                "Confluence_Passed",
                "Confluence_Alignment",
                "Filtered",
                "Signal",
                "Bias",
                "Fast_MA",
                "Slow_MA",
            }
            return {
                "rows": len(stats),
                "latest": {
                    key: Backtest._json_safe(value)
                    for key, value in latest.items()
                    if key in keep
                },
            }
        return {"type": type(stats).__name__}

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if hasattr(value, "item"):
            return value.item()
        if isinstance(value, (pd.Timestamp, datetime)):
            return value.isoformat()
        return value

    @staticmethod
    def _has_usable_data(data: Any) -> bool:
        if data is None:
            return False
        if isinstance(data, dict):
            return any(isinstance(df, pd.DataFrame) and not df.empty for df in data.values())
        if isinstance(data, pd.DataFrame):
            return not data.empty
        return bool(data)

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None or pd.isna(value):
                return default
            value = float(value)
            if value == float("inf"):
                return 3.0
            return value
        except Exception:
            return default

    @staticmethod
    def _normalize(value: float, min_v: float, max_v: float) -> float:
        if max_v == min_v:
            return 0.0
        return max(0.0, min((value - min_v) / (max_v - min_v), 1.0))


BackTest = Backtest
