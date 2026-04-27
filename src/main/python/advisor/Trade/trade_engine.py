import asyncio
from datetime import datetime, timedelta, timezone

from advisor.Trade.RiskManager import RiskManager
from advisor.Trade.tradeHandler import mt5TradeHandler
from advisor.Trade.trateState import TradeStateManager
from advisor.core.health_bus import HealthBus
from advisor.core.event_bus import EventBus
from advisor.core.portfolio.portfolio_manager import PortfolioManager
from advisor.core import events
from advisor.core.state import BotLifecycle, StateManager
from Strategy_model.signals.signal_store import SignalStore
from advisor.scheduler.process_sceduler import ProcessScheduler
from advisor.scheduler.requirements import ProcessRequirement
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.utils.logging_setup import get_logger

logger = get_logger("Trade_Executor")

EXECUTION_REQS = [
    ProcessRequirement("signals", max_age=timedelta(minutes=2)),
    ProcessRequirement("symbol_ingestion", max_age=timedelta(minutes=5)),
]


class ExecutionProcess:
    name = "execution"

    def __init__(
        self,
        client,
        signal_store: SignalStore,
        health_bus: HealthBus,
        heartbeats: dict,
        shutdown_event,
        scheduler: ProcessScheduler,
        state_manager: StateManager,
        symbol_watch: SymbolWatch,
        event_bus: EventBus,
        trade_state: TradeStateManager | None = None,
        portfolio_manager: PortfolioManager | None = None,
    ):
        self.client = client
        self.signal_store = signal_store
        self.health_bus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event
        self.scheduler = scheduler
        self.symbol_watch = symbol_watch
        self.event_bus = event_bus
        self.state_manager = state_manager

        self.trade_state = trade_state or TradeStateManager(self.client)

        self.risk_manager = RiskManager(
            client=self.client,
            trade_state=self.trade_state,
            state_manager=self.state_manager,
            health_bus=self.health_bus,
        )

        self.executor = mt5TradeHandler(self.client, logger)
        self.portfolio_manager = portfolio_manager or PortfolioManager(
            capital=self._portfolio_capital(),
            risk_per_trade=getattr(self.risk_manager, "max_risk_per_trade", 0.01),
            max_positions=5,
            max_symbol_exposure=0.2,
        )

        self._running: set[str] = set()
        self._processed: set[str] = set()
        self.processed_signals = self._processed
        self._subscribed_symbols: set[str] = set()
        self._portfolio_lock = asyncio.Lock()

    def register(self):
        self.event_bus.subscribe(events.SYMBOLS, self._on_symbols)
        self.event_bus.subscribe(
            events.SIGNAL_GENERATED,
            lambda evt: asyncio.create_task(self._on_signal(None, evt)),
        )
        for symbol in self.symbol_watch.all_symbol_names():
            self._subscribe_symbol(symbol)

    def _subscribe_symbol(self, symbol: str) -> None:
        if symbol in self._subscribed_symbols:
            return
        self._subscribed_symbols.add(symbol)
        self.event_bus.subscribe(
            f"{events.SIGNAL_GENERATED}:{symbol}",
            lambda evt, s=symbol: asyncio.create_task(self._on_signal(s, evt)),
        )

    def _on_symbols(self, event) -> None:
        symbols = []
        if event and getattr(event, "payload", None):
            payload_symbols = event.payload.get("symbols")
            if isinstance(payload_symbols, list):
                symbols = payload_symbols
        if not symbols:
            symbols = self.symbol_watch.all_symbol_names()
        for symbol in symbols:
            self._subscribe_symbol(symbol)

    async def _on_signal(self, symbol: str | None, event):
        if symbol is None and event and getattr(event, "payload", None):
            symbol = event.payload.get("symbol")
        if self.stop_event.is_set() or not symbol:
            return

        if symbol in self._running:
            return

        self._running.add(symbol)

        try:
            await self.scheduler.schedule(
                process_name=f"{self.name}:{symbol}",
                required_resources=[],
                task=lambda: self._execute_symbol(symbol, event.payload),
                shutdown_event=self.stop_event,
                heartbeats=self.heartbeats,
                timeout=30,
            )
        except Exception as e:
            logger.exception("Execution failed for %s: %s", symbol, e)
            self.state_manager.set_state(BotLifecycle.DEGRADED)
            self.symbol_watch.mark_error(symbol, str(e))
            self.health_bus.update(f"{self.name}:{symbol}", "ERROR", {"error": str(e)})
        finally:
            self._running.discard(symbol)

    async def _execute_symbol(self, symbol: str, payload: dict):
        try:
            async with self._portfolio_lock:
                signal_id = payload.get("id")
                if not signal_id or signal_id in self._processed:
                    return

                state = self.symbol_watch.get(symbol)
                if state is not None and not getattr(state, "enabled", False):
                    return

                signal = self.signal_store.get_latest(symbol)
                if not signal or not signal.is_valid():
                    return

                portfolio_trade = self._select_portfolio_trade(symbol)
                if portfolio_trade is None:
                    return

                allowed, lot = self.risk_manager.validate(signal)
                if not allowed:
                    return

                trade = self.executor.place_market_order(
                    symbol=signal.symbol,
                    side=signal.side,
                    lot=lot,
                    sl_points=signal.sl,
                    tp_points=signal.tp,
                )
                trade_record = self._build_trade_record(trade, signal, lot, portfolio_trade)
                self.trade_state.register_open(trade_record)
                self.risk_manager.register_trade_open()
                self._sync_portfolio_positions()

                self._processed.add(signal_id)
                self.symbol_watch.mark_trade(symbol)

                self.heartbeats[f"{self.name}:{symbol}"] = datetime.now(timezone.utc).isoformat()
                self.health_bus.update(
                    f"{self.name}:{symbol}",
                    "RUNNING",
                    {
                        "symbol": symbol,
                        "executed": 1,
                        "confidence": portfolio_trade.get("confidence"),
                        "portfolio_size": portfolio_trade.get("position_size"),
                    },
                )

                await self.event_bus.publish(
                    f"{events.ORDER_EXECUTED}:{symbol}",
                    {
                        "symbol": symbol,
                        "trade": str(trade),
                        "portfolio": portfolio_trade,
                    },
                )
        except Exception as e:
            logger.exception("Execution failed for %s: %s", symbol, e)
            self.symbol_watch.mark_error(symbol, str(e))

    def _select_portfolio_trade(self, symbol: str) -> dict | None:
        self._sync_portfolio_positions()
        self.portfolio_manager.capital = self._portfolio_capital()
        self.portfolio_manager.risk_per_trade = getattr(
            self.risk_manager,
            "max_risk_per_trade",
            self.portfolio_manager.risk_per_trade,
        )
        self.portfolio_manager.signal_pool.clear()

        latest_signals = self.signal_store.snapshot_latest(max_age_minutes=2)
        for candidate_symbol, raw in latest_signals.items():
            portfolio_signal = self._portfolio_signal_from_raw(candidate_symbol, raw)
            if portfolio_signal is None:
                continue
            self.portfolio_manager.add_signal(candidate_symbol, portfolio_signal)

        trades = self.portfolio_manager.build_portfolio()
        for trade in trades:
            if trade.get("symbol") == symbol:
                return trade
        return None

    def _portfolio_signal_from_raw(self, symbol: str, raw: dict) -> dict | None:
        if not isinstance(raw, dict):
            return None

        side = str(raw.get("side") or raw.get("direction") or "").strip().lower()
        if side not in {"buy", "sell"}:
            return None

        data = raw.get("data", {}) if isinstance(raw.get("data"), dict) else {}
        metadata = dict(data)
        metadata.setdefault("signal_id", raw.get("id"))
        metadata.setdefault("sl", raw.get("sl"))
        metadata.setdefault("tp", raw.get("tp"))
        metadata.setdefault("sl_distance", raw.get("sl", metadata.get("sl_distance", 0)))

        return {
            "symbol": symbol,
            "direction": side.title(),
            "confidence": raw.get("confidence", metadata.get("confidence", 50.0)),
            "metadata": metadata,
        }

    def _sync_portfolio_positions(self) -> None:
        getter = getattr(self.trade_state, "get_active_trades", None)
        if not callable(getter):
            self.portfolio_manager.sync_active_positions({})
            return

        active = []
        for trade in getter() or []:
            if not isinstance(trade, dict):
                continue
            portfolio = trade.get("portfolio", {}) if isinstance(trade.get("portfolio"), dict) else {}
            position_size = portfolio.get("position_size", trade.get("volume", 0.0))
            active.append(
                {
                    "symbol": trade.get("symbol"),
                    "position_size": position_size,
                    "volume": trade.get("volume", 0.0),
                }
            )
        self.portfolio_manager.sync_active_positions(active)

    def _build_trade_record(
        self,
        trade,
        signal,
        lot: float,
        portfolio_trade: dict,
    ) -> dict:
        if isinstance(trade, dict):
            trade_record = dict(trade)
        else:
            trade_record = {
                "ticket": trade,
                "symbol": signal.symbol,
                "side": signal.side,
                "volume": float(lot),
            }
        trade_record["portfolio"] = portfolio_trade
        trade_record.setdefault("symbol", signal.symbol)
        trade_record.setdefault("side", signal.side)
        trade_record.setdefault("volume", float(lot))
        return trade_record

    def _portfolio_capital(self) -> float:
        getter = getattr(self.client, "get_equity", None)
        if callable(getter):
            try:
                equity = float(getter())
                if equity > 0:
                    return equity
            except Exception:
                pass

        info = getattr(self.client, "account_info", None)
        if isinstance(info, dict):
            try:
                equity = float(info.get("equity", info.get("balance", 0.0)))
                if equity > 0:
                    return equity
            except Exception:
                pass

        existing_manager = getattr(self, "portfolio_manager", None)
        existing_capital = getattr(existing_manager, "capital", 0.0) if existing_manager is not None else 0.0
        return max(float(existing_capital or 0.0), 10000.0)
