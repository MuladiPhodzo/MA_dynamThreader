# 📈 MovingAverage Advisor Bot

```markdown

The **MovingAverage Advisor** is an automated trading bot designed to analyze market trends using 
multi-timeframe moving average crossover strategies. It connects to **MetaTrader 5 (MT5)** and 
makes buy/sell decisions based on real-time price data and 
calculated signals.

---

## bot constraints

┌────────────────────────────────────────┐
| Dimension           | Bot              |
| ------------------- | ---------------- |
| Symbols             | 10–200           |
| Timeframes          | ~8               |
| Data type           | OHLC (not ticks) |
| Backtest frequency  | Every 3 months   |
| Runtime             | Single machine   |
| Concurrency         | Multi-process    |
| Latency sensitivity | Medium           |
| Statefulness        | High             |
└────────────────────────────────────────┘

## High-Level Layer Overview
```markdown

    ┌────────────────────────────────────────────┐
    │ 1. Infrastructure Layer                    │
    ├────────────────────────────────────────────┤
    │ 2. Data Acquisition Layer                  │
    ├────────────────────────────────────────────┤
    │ 3. Data Management Layer                   │
    ├────────────────────────────────────────────┤
    │ 4. Feature Engineering Layer               │
    ├────────────────────────────────────────────┤
    │ 5. Market Structure & Context Layer        │
    ├────────────────────────────────────────────┤
    │ 6. Signal Generation Layer                 │
    ├────────────────────────────────────────────┤
    │ 7. Strategy Orchestration Layer            │
    ├────────────────────────────────────────────┤
    │ 8. Risk & Trade Management Layer           │
    ├────────────────────────────────────────────┤
    │ 9. Execution Layer                         │
    ├────────────────────────────────────────────┤
    │ 10. Backtesting & Simulation Layer         │
    ├────────────────────────────────────────────┤
    │ 11. Performance & Analytics Layer          │
    ├────────────────────────────────────────────┤
    │ 12. Persistence & Reporting Layer          │
    ├────────────────────────────────────────────┤
    │ 13. Monitoring & Observability Layer       │
    └────────────────────────────────────────────┘
```

## 🧠 Key Features

- Graphical user interface
- Multi-timeframe  strategy support
- Moving Average crossover-based signal generation
- Automated decision-making and trade execution
- Threaded execution for handling multiple symbols concurrently
- Customizable trade thresholds and timeframes
- Modular and extensible Python codebase

---

## 📁 Project Structure

```scss

MovingAverage_Advisor/
├── advisor/                            # Core logic
|   ├──
|   ├── Client/
│   |   ├── __init__.py
|   │   └── mt5Client.py
|   ├── GUI/
│   |   ├── __init__.py
|   │   └── userInput.py
│   ├── Trade/
│   |   ├── __init__.py
│   |   ├── statsManager.py
│   │   └── TradesAlgo.py               # Trade execution logic
│   ├── database/
│   |   ├── __init__.py
│   |   └── MySQLdatabase.py            # Optional DB logging
|   ├── Telegram/
│   |   └── __init__.py
│   |       ├── core.py                 # main TelegramMessenger (async)
│   |       ├── runner.py               # bot startup with lock + watchdog
│   |       ├── handlers/
│   |       │   ├── __init__.py
│   |       │   ├── start_handler.py
│   |       │   ├── stop_handler.py
│   |       │   ├── status_handler.py
│   |       └── utils/
│   |           ├── logger.py           # rotating logs
│   |           ├── singleton.py        # PID lock + stale cleanup
│   |           ├── env_loader.py       # unified .env resolver
│   |           └── healthcheck.py      # simple health server
|   ├── utils/
|   │   ├── __init__.py
│   |   ├── cache.py
│   |   ├── ThreadHandler.py
│   |   └── dataHandler.py  
|   │
|   ├── MovingAverage/
│   |   ├── __init__.py
|   │   └── MovingAverage.py            # Strategy implementation
|   │
│   └── MA_DynamAdvisor.py              # Main bot runner
├── .env
├── makefile
├── build.py                            # PyBuilder build script
├── Dockerfile                          # Docker container config
├── requirements.txt                    # Python dependencies
├── README.md                           # Project documentation
└── .pybuilder/                         # PyBuilder generated files

```

## system process diagram

```scss
                             ┌───────────────────────────────────────────────────────┐
[start] ────────────────────>│         APP BOOTSTRAP                                 │
                             │-------------------------------------------------------│
                             │ - Load configs.json                                   │
                             │ - Load bot_state.json                                 │
                             │ - Init MT5 client                                     │
                             │ - Verify account                                      │
                             │  (src/main/python/advisor/bootstrap/sys_bootstrap.py) |
                             └───────────────────────────────────────────────────────┘
                                             |
                                             | (config + state + MT5 client)
                                             ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│                                            MAIN                                              │
│----------------------------------------------------------------------------------------------│
│ - StateManager (bot lifecycle + bot state)                                                   │
│   (src/main/python/advisor/core/state.py)                                                    │
│ - SymbolWatch (active + all symbols, telemetry)                                              │
│   (src/main/python/advisor/Client/symbols/symbol_watch.py)                                   │
│ - SignalStore                                                                                │
│   (src/main/python/advisor/indicators/signal_store.py)                                       │
│ - TradeStateManager                                                                          │
│   (src/main/python/advisor/Trade/trateState.py)                                              │
│ - CacheManager                                                                               │
│   (src/main/python/advisor/utils/dataHandler.py)                                             │
│ - ProcessScheduler (ReadinessGate + ResourceRegistry)                                        │
│   (src/main/python/advisor/scheduler/process_sceduler.py)                                    │
│ - HealthBus (shared process health)                                                          │
│   (src/main/python/advisor/core/health_bus.py)                                               │
│ - HeartbeatRegistry                                                                          │
│   (src/main/python/advisor/process/heartbeats.py)                                            │
│ - DashboardServer (FastAPI/Uvicorn thread)                                                   │
│   (src/main/python/advisor/api/server.py)                                                    │
│----------------------------------------------------------------------------------------------│
│                           Supervisor (threaded process manager)                              │
│      ┌────────────────────────────────────────────────────────────────────────────────────┐  │
│      │ - register_process()                                                               │  │
│      │ - dependency graph (start order + dependency enforcement)                          │  │
│      │ - start/stop/restart                                                               │  │
│      │ - heartbeat monitor + periodic status log                                          │  │
│      │  (src/main/python/advisor/process/process_engine.py)                               │  │
│      └────────────────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
                                             |
                                             | dependency graph (startup order)
                                             | pipeline -> backtest -> strategy -> execution
                                             ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│                           SCHEDULER READINESS GATES (ProcessScheduler)                       │
│----------------------------------------------------------------------------------------------│
│ ReadinessGate checks ResourceRegistry before running each cycle:                             │
│ - pipeline  : requires []                                                                    │
│ - backtest  : requires market_data                                                           │
│ - strategy  : requires market_data                                                           │
│ - execution : requires signals                                                               │
│----------------------------------------------------------------------------------------------│
│ ResourceRegistry is updated by processes when a resource becomes ready:                      │
│ - pipeline  -> sets market_data                                                              │
│ - backtest  -> sets backtest_data + symbols                                                  │
│ - strategy  -> sets signals                                                                  │
│----------------------------------------------------------------------------------------------│
│ Files:                                                                                       │
│ - ReadinessGate                                                                              │
│   (src/main/python/advisor/scheduler/readiness_gate.py)                                      │
│ - ResourceRegistry                                                                           │
│   (src/main/python/advisor/scheduler/resource_registry.py)                                   │
│ - ProcessRequirement                                                                         │
│   (src/main/python/advisor/scheduler/requirements.py)                                        │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
                                             |
                                             ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│                                      PROCESS THREADS                                         │
│----------------------------------------------------------------------------------------------│
│ Process 1: pipeline                                                                          │
│ - Ingest market data via MT5                                                                 │
│ - Cache data                                                                                │
│ - Registry: set_ready("market_data")                                                         │
│ - Heartbeats + HealthBus                                                                     │
│ (src/main/python/advisor/mt5_pipeline/runner.py)                                              │
│ (src/main/python/advisor/mt5_pipeline/core.py)                                                │
│----------------------------------------------------------------------------------------------│
│ Process 2: backtest                                                                          │
│ - Runs if 90 days elapsed                                                                    │
│ - Uses cache + MT5 fetches                                                                   │
│ - Registry: set_ready("backtest_data"), set_ready("symbols")                                 │
│ - Heartbeats + HealthBus                                                                     │
│ (src/main/python/advisor/backtest/engine.py)                                                  │
│ (src/main/python/advisor/backtest/core.py)                                                    │
│----------------------------------------------------------------------------------------------│
│ Process 3: strategy                                                                          │
│ - Requires market_data (gate)                                                                │
│ - Generates signals into SignalStore                                                         │
│ - Registry: set_ready("signals")                                                             │
│ - Heartbeats + HealthBus                                                                     │
│ (src/main/python/advisor/indicators/strategy.py)                                              │
│ - Strategy implementations                                                                   │
│   (src/main/python/advisor/indicators/MA/MovingAverage.py)                                   │
│   (src/main/python/advisor/indicators/Volume/volumeindex.py)                                 │
│----------------------------------------------------------------------------------------------│
│ Process 4: execution                                                                         │
│ - Requires signals (gate)                                                                    │
│ - RiskManager + trade execution                                                              │
│ - Heartbeats + HealthBus                                                                     │
│ (src/main/python/advisor/Trade/trade_engine.py)                                               │
│ - Trade handler                                                                              │
│   (src/main/python/advisor/Trade/tradeHandler.py)                                             │
│ - Risk manager                                                                               │
│   (src/main/python/advisor/Trade/RiskManager.py)                                              │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
                                             |
                                             ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   SERVICE LAYER (MT5 + DATA)                                 │
│----------------------------------------------------------------------------------------------│
│ MetaTrader5 client                                                                           │
│ - Connect/login                                                                              │
│ - Fetch symbols + data                                                                       │
│ (src/main/python/advisor/Client/mt5Client.py)                                                │
│                                                                                              │
│ Cache + data handler                                                                         │
│ (src/main/python/advisor/utils/dataHandler.py)                                               │
│                                                                                              │
│ Symbol state + telemetry                                                                     │
│ (src/main/python/advisor/Client/symbols/symbol_watch.py)                                     │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
                                             |
                                             ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   DASHBOARD API (FastAPI)                                    │
│----------------------------------------------------------------------------------------------│
│ /status           -> HealthBus + Supervisor snapshot + Symbol telemetry + Bot state          │
│ /symbols          -> list symbols                                                            │
│ /symbols/{sym}    -> toggle symbol enabled                                                   │
│ /processes/*      -> start/stop/restart                                                      │
│ /config/reload    -> reload bot state + refresh SymbolWatch                                  │
│ /backtest/run     -> reset backtest timer                                                    │
│ (src/main/python/advisor/api/server.py)                                                      │
└──────────────────────────────────────────────────────────────────────────────────────────────┘

```

## 🚀 Getting Started

### 🧰 Prerequisites

---

## Dev-requirements

- Python **3.10+**
- MetaTrader5 terminal installed and configured
- Docker

## client rewuirements

- MetaTrader5 terminal **installed**
- good **connectivity**
- Algo tradinig **enabled** on the Metatrader terminal

---

## dashboard layout design

```mathematica
┌─────────────────────────────────────────────┐
│ Header: Account | Bot Status | Time | User  │
├───────────┬─────────────────────────────────┤
│ Sidebar   │ Main Content Area               │
│           │                                 │
│ - Account │  (Active Panel)                 │
│ - Symbols │                                 │
│ - Bot     │                                 │
│ - Logs    │                                 │
│ - Store   │                                 │
│ - Support │                                 │
└───────────┴─────────────────────────────────┘
```

## 📝 Todo

- Integrate database logging (MySQL/PostgreSQL)
- Implement scheduled backtesting
- Implement Risk management module
- Add support for alternative strategies
- Implement stats module
- Implement tracing stoploss

---

## 🧠 Author

**Phodzo Lionel Muladi**  
**Email**: <muladi.lionel@gmail.com>  
**LinkedIn**: <https://www.linkedin.com/in/phodzo-muladi-654214257>

---

## 📜 License

This project is licensed under the MIT License
