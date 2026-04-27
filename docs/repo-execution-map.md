# MovingAverage Advisor Repo Execution Map

This document maps the current repository by dependency layer, runtime entrypoint,
call chain, event flow, and known responsiveness/operability risks.

## External Dependencies

Python runtime dependencies are declared in `requirements.txt`.

Core runtime dependencies:

- `MetaTrader5`: terminal connection, account data, symbols, bars, order/deal history.
- `pandas`, `numpy`: OHLC dataframes, indicators, scoring, confluence logic.
- `fastapi`, `uvicorn`, `pydantic`: dashboard API.
- `python-telegram-bot`, `requests`: Telegram integration.
- `ttkbootstrap`, `tkinter`: setup wizard.
- `psutil`: singleton/startup process checks.

Build/test/tooling dependencies:

- `pytest`, `pytest-asyncio`, `pytest-cov`, `coverage`.
- `pyinstaller`, `pybuilder`, `hatch`, `setuptools`.
- `flake8`, `pylint`, `autopep8`, `isort`.

Dashboard dependencies are declared in `dashboard/package.json`.

Core frontend dependencies:

- Angular 17.3.8 packages.
- `rxjs` for polling and request streams.
- `zone.js`, `tslib`.

## Source Layout

Primary backend package:

- `src/main/python/advisor/__main__.py`: process entrypoint.
- `src/main/python/advisor/MA_DynamAdvisor.py`: runtime composition root.
- `src/main/python/advisor/api/server.py`: FastAPI dashboard API.
- `src/main/python/advisor/Client/mt5Client.py`: MT5 terminal adapter.
- `src/main/python/advisor/mt5_pipeline/`: market data ingestion.
- `src/main/python/advisor/Strategy_model/`: strategy config, tools, scoring, signals.
- `src/main/python/advisor/backtest/`: simulation and backtest orchestration.
- `src/main/python/advisor/Trade/`: risk, order placement, trade state, execution process.
- `src/main/python/advisor/core/`: state, events, health, locks, persisted flow state.
- `src/main/python/advisor/process/`: threaded supervisor and heartbeats.
- `src/main/python/advisor/scheduler/`: async task scheduling/readiness resources.

Primary dashboard package:

- `dashboard/src/app/api.service.ts`: HTTP client wrapper for API routes.
- `dashboard/src/app/app.component.ts`: dashboard polling, panel state, account/strategy UI state.
- `dashboard/src/app/app.component.html`: all panel templates.
- `dashboard/src/app/app.component.css`: responsive layout and visual styling.
- `dashboard/src/app/models.ts`: API response and UI model types.

Runtime/state files:

- `configs.json`: credentials/trade config/account defaults/strategy configs.
- `bot_state.json`: symbol state, lifecycle state, backtest timestamps, symbol metadata.
- `runtime/strategy_registry.json`: strategy configs, symbol attachments, signal/backtest stats.
- `runtime/flow_state.json`: signal/execution continuity.
- `runtime/supervisor_state.json`: supervisor restart counts.
- `runtime/support_tickets.jsonl`: support tickets.
- `stats/trading_stats.csv`, `trades/trades_log.*`: optional account activity sources.
- `MA_DynamAdvisor.log`: process log.

## Runtime Entry Flow

```text
python -m advisor
  -> advisor.__main__.main()
     -> configure logging and exception hooks
     -> ensure single instance lock
     -> Main()
        -> SyncManager, EventBus, ProcessScheduler, HeartbeatRegistry
        -> SystemBootstrap loads configs.json and bot_state.json
        -> SymbolWatch snapshots symbols and telemetry
        -> Supervisor created
     -> Main.initialize()
        -> MetaTrader5Client.initialize(config.creds)
        -> construct pipeline, strategy, execution, dashboard
        -> seed/sync symbols from MT5 if needed
        -> normalize symbol metadata
     -> Main.start()
        -> restore open positions
        -> start FastAPI dashboard thread
        -> Supervisor.start()
```

## Process Topology

Registered processes:

```text
pipeline  -> threaded process, fixed polling loop
strategy  -> event-driven, subscribed to MARKET_DATA_READY events
execution -> event-driven, subscribed to SIGNAL_GENERATED events
```

The `backtest` process class exists in `advisor/backtest/engine.py`, but it is
currently not registered in `MA_DynamAdvisor.py`. Backtests are still triggerable
through the API via `run_backtest()`, but the long-lived `BacktestProcess`
event subscriber is commented out.

Supervisor behavior:

- Non-event processes are daemon threads with heartbeat timers.
- Event-driven processes do not get their own OS/thread process from the supervisor.
- `/status` reports event-driven process `running: true` with `pid: null` by design.

## Event Bus Flow

Event names live in `advisor/core/events.py`:

```text
CONNECTED
SYMBOLS
MARKET_DATA_READY
RUN_BACKTEST
BACKTEST_COMPLETED
SIGNAL_GENERATED
ORDER_CREATED
ORDER_EXECUTED
STRATEGY_CONFIG_UPDATED
STRATEGY_REGISTRY_UPDATED
```

Market data event chain:

```text
pipelineProcess.run()
  -> _run_poll_cycle()
  -> scheduler.schedule("pipeline")
  -> MarketDataPipeline.run_once()
  -> MetaTrader5Client.get_multi_tf_data(symbol)
  -> CacheManager.set_atomic(symbol, timeframe data)
  -> SymbolWatch.mark_data_fetch(symbol)
  -> event_bus.emit("market_data_ready:<symbol>")
  -> event_bus.publish("market_data_ready", {symbols, telemetry})
```

Strategy event chain:

```text
StrategyManager.register()
  -> subscribe("market_data_ready")
  -> subscribe("market_data_ready:<symbol>") for known symbols

market_data_ready:<symbol>
  -> StrategyManager._spawn_market_data_task()
  -> _on_market_data(symbol)
  -> scheduler.schedule("strategy:<symbol>")
  -> _run_symbol(symbol)
  -> StrategyModel(symbol, cache).run()
  -> normalize signal
  -> SignalStore.add_signal()
  -> SymbolWatch.mark_signal()
  -> publish("signal_generated")
  -> publish("signal_generated:<symbol>")
```

Execution event chain:

```text
ExecutionProcess.register()
  -> subscribe("symbols")
  -> subscribe("signal_generated")
  -> subscribe("signal_generated:<symbol>")

signal_generated
  -> _on_signal(symbol)
  -> scheduler.schedule("execution:<symbol>")
  -> _execute_symbol()
  -> SignalStore.get_latest(symbol)
  -> PortfolioManager.build_portfolio()
  -> RiskManager.validate()
  -> mt5TradeHandler.place_market_order()
  -> TradeStateManager.register_open()
  -> publish("order_executed:<symbol>")
```

Backtest API/event chain:

```text
dashboard -> POST /api/backtest/run
  -> server.run_backtest()
  -> emits RUN_BACKTEST / RUN_BACKTEST:<strategy>
  -> optionally uses backtest helpers directly depending current server path

BacktestProcess, if registered:
  -> subscribe RUN_BACKTEST
  -> Backtest.run_symbol() or Backtest.run_top_symbols()
  -> StrategyModel(...).run(backtest=True)
  -> TradingSimulator.simulate()
  -> update bot_state last_backtest
  -> publish BACKTEST_COMPLETED
```

## Dashboard API Map

Angular service calls:

```text
ApiService.getStatus()          -> GET  /api/status
ApiService.getAccountHistory()  -> GET  /api/account/history
ApiService.getStrategyCatalog() -> GET  /api/strategy/catalog
ApiService.getStrategyList()    -> GET  /api/strategy/list
ApiService.createStrategy()     -> POST /api/strategy/create
ApiService.runBacktest()        -> POST /api/backtest/run
ApiService.toggleSymbol()       -> POST /api/symbols/{symbol}/toggle
ApiService.reloadConfig()       -> POST /api/config/reload
ApiService.startProcess()       -> POST /api/processes/{name}/start
ApiService.stopProcess()        -> POST /api/processes/{name}/stop
ApiService.restartProcess()     -> POST /api/processes/{name}/restart
ApiService.createSupportTicket()-> POST /api/support/ticket
ApiService.getSupportKb()       -> GET  /api/support/kb
```

Frontend polling:

```text
ngOnInit()
  -> loadBacktestSelection()
  -> loadStrategyForm()
  -> loadStrategyCatalog()
  -> compileStrategyPreview()
  -> every 2s:  getStatus()
  -> every 10s: getAccountHistory()
  -> every 10s: getStrategyList()
```

Panel rendering:

```text
activePanel = account | symbols | strategy | bot | logs | store | support
  -> app.component.html uses *ngIf per panel
```

The dashboard is currently a monolithic component. Even when the Strategy panel
is not open, the catalog request is made on dashboard startup.

## Strategy Catalog Flow

```text
dashboard ngOnInit()
  -> loadStrategyCatalog()
  -> ApiService.getStrategyCatalog()
  -> GET /api/strategy/catalog
  -> server.strategy_tool_catalog()
     -> load_builtin_indicators()
     -> StrategyModel.DEFAULT_CONFIG
     -> IndicatorRegistry._REGISTRY
     -> TechnicalRegistry._REGISTRY
     -> PatternRegistry._REGISTRY
     -> inspect constructors for parameter defaults
     -> response {defaults, timeframes, indicators, technical, patterns}
```

Registered strategy tools:

- Indicators: `ma`, `macd`, `ao`, `atr`, `rsi`.
- Technical tools: `fvg`, `market structure`, `liquidity`, `obd`.
- Patterns: `quasimodo`, `head_and_shoulders`, `double_pattern`.

Strategy panel risk findings:

- The panel waits on `/strategy/catalog` before tool controls appear.
- There is no explicit loading state for the tool catalog, so slow load can look like missing controls.
- The catalog is rebuilt on every request; there is no backend cache.
- Indicators are loaded by `load_builtin_indicators()` per request.
- Technical and pattern registries rely on import side effects at module import time.
- If any registry import fails, tools can be partially missing.
- The frontend renders an empty-tools message when catalog options are empty, even if the request is still in flight.
- The strategy panel shares one large component with status/history/registry polling, so change detection work is broader than needed.

Recommended fix path:

1. Add backend catalog caching with explicit invalidation on `STRATEGY_CONFIG_UPDATED`.
2. Add a `strategyCatalogLoading` flag in the dashboard and show skeleton/loading controls.
3. Lazy-load catalog when entering the strategy panel, then retain it.
4. Add a backend registry health payload: counts and missing import errors.
5. Add a small API test asserting the catalog includes the expected 5 indicators, 4 technical tools, and 3 pattern families.

## MT5 Client Flow

```text
MetaTrader5Client.initialize(creds)
  -> mt5.initialize()
  -> connect_account()
     -> mt5.login()
     -> mt5.account_info()
     -> mt5.terminal_info()
  -> get_Symbols()
     -> mt5.symbols_get()
```

Market data:

```text
get_multi_tf_data(symbol, backtest)
  -> for each timeframe
     -> _should_fetch_tf(symbol, tf)
     -> get_live_data(symbol, timeframe, bars)
        -> _ensure_symbol_selected()
        -> mt5.copy_rates_from_pos()
        -> pandas DataFrame
        -> UTC time normalization
```

Account data:

```text
get_account_snapshot()
  -> account_info dict

get_account_deals(utc_from, utc_to)
  -> mt5.history_deals_get()
  -> list[dict]
```

## Account Dashboard Flow

```text
dashboard every 10s
  -> GET /api/account/history
  -> account_history()
     -> read stats/trading_stats.csv if present
     -> else read trades/trades_log.jsonl/json
     -> else read MT5 terminal history deals
     -> else build fallback from bot_state.json
     -> _safe_account_snapshot()
     -> _build_summary(points)
     -> _build_account_activity(rows, points)
  -> updateAccountHistory()
  -> equity chart, daily P/L, trades per day, cashflows
```

Data precedence:

1. `stats/trading_stats.csv`
2. `trades/trades_log.jsonl` or `trades/trades_log.json`
3. MT5 terminal deal history
4. `bot_state.json` fallback

## Strategy Execution Internals

```text
StrategyModel.__init__()
  -> deep copy config or DEFAULT_CONFIG
  -> DataHandler(symbol, strategy_name, cache)
  -> normalize timeframes/tool config
  -> _initialize_tools()
     -> TechnicalRegistry.build()
     -> load_builtin_indicators()
     -> IndicatorRegistry.build()
     -> PatternRegistry.build()
  -> SMCFeatureEngine
  -> ConfluenceEngine
  -> ScoringEngine
  -> SignalFilter
  -> SignalDecision
```

Batch run:

```text
run()
  -> _run_batch()
     -> _process_timeframes()
        -> _apply_indicators_by_role()
     -> _apply_smc_features()
     -> _build_features()
     -> _score()
     -> _filter()
     -> _decide()
     -> _build_signal_from_entry_frame()
```

Live run:

```text
enable_live_mode()
  -> _seed_live_states()

run()
  -> _run_live_snapshot()
     -> on_new_candle(role, candle)
        -> IndicatorEngine.update()
        -> SMCEventEngine.update()
        -> FeatureEngine.build()
        -> RealTimeConfluence.compute()
```

## Import/Dependency Shape

Highest-level dependency direction:

```text
__main__
  -> MA_DynamAdvisor
     -> bootstrap/config/state
     -> mt5Client
     -> SymbolWatch
     -> EventBus / ProcessScheduler / Supervisor
     -> pipelineProcess
     -> StrategyManager
     -> ExecutionProcess
     -> DashboardServer

api.server
  -> StateManager, SymbolWatch, Supervisor, EventBus, HealthBus
  -> StrategyModel, StrategyRegistry, tool registries

pipeline
  -> mt5Client, SymbolWatch, CacheManager, EventBus, Scheduler

strategy_runner
  -> StrategyModel, StrategyRegistry, SignalStore, SymbolWatch, EventBus, Scheduler

trade_engine
  -> RiskManager, tradeHandler, TradeStateManager, PortfolioManager, SignalStore, EventBus

strategy.py
  -> registries, indicators, technical tools, patterns, signal engines, cache/data handlers
```

Import fragility findings:

- Some modules import with `advisor.*`; others still import top-level `Strategy_model` or `utils`.
- This is why startup depends on `__main__.py` adding both `src/main/python` and `src/main/python/advisor` to `sys.path`.
- Long term, normalize imports to `advisor.*` to reduce hidden path coupling.

## Critical Behavioral Risks

1. Strategy panel tools can appear missing while `/strategy/catalog` is slow or returns partial registry data.
2. `/strategy/catalog` currently has no cache and does registry introspection on demand.
3. Backtest process code exists but is not registered in the main runtime composition.
4. Event-driven processes show `pid: null`; this is correct but can look broken in the UI.
5. Symbols default to disabled after MT5 sync; strategy subscribes, but signals and trades only flow for enabled symbols.
6. Pipeline ingests all symbols on the first cycle, which can be slow with many MT5 symbols.
7. There are two cache-facing modules: `utils/dataHandler.py` and `utils/cache_handler.py`; this makes ownership harder to reason about.
8. Dashboard `AppComponent` owns all panels, polling, forms, charts, and controls; responsiveness work will get harder as features grow.
9. Account history is read-only aggregation; MT5 terminal history can seed stats, but the app does not yet persist normalized account-history snapshots.
10. Runtime files are mixed at repo root and `runtime/`, so generated state can easily pollute git status.

## Priority Stabilization Plan

1. Cache and instrument `/strategy/catalog`.
2. Add dashboard loading/error states for the strategy catalog.
3. Lazy-load heavy panel data on panel activation.
4. Normalize imports to the `advisor.*` namespace.
5. Decide whether `BacktestProcess` should be re-enabled or removed from the visible process model.
6. Persist normalized account activity snapshots after MT5 history fallback.
7. Split `AppComponent` into account, symbols, strategy, bot, logs, support panels.
8. Move runtime/cache/generated paths firmly out of tracked source.
