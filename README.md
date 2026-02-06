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
                            ┌───────────────────────────┐           ┌───────────────────┐
[strat]>───────────────────>|       APP BOOTSTRAP       |      None |   setUp Wizard    |
                            |---------------------------|     ┌────>|-------------------|
                            | - Fetch configs           | cfg |     |- manual config    |
                            |  └── validate configs <>────────┘┐    |                   |
                            └───────────────────────────┘      |    └───────────────────┘
                                                               |               |new_configs
                ┌──────────────────────────────────────────────┘<──────────────┘
                |                           
                |
                |
                |                                                 Shared Cache (Authoritative)
Main            ▼                      ┌───────────────────────────────────────────────────────────────────────────────────────────────┐
┌───────────────────────────┐          |                                                                                               |
|                           |          |  Process1: mt5 pipeline                                                                       |
| Process                   |          |  ┌─────────────────────────────────────────────────────┐                                      |
| Supervisor                |       ┌────>|params: user_data, cache_handler                     |                                      |
|                           |       |  |  │-----------------------------------------------------│                                      |
|                           |       |  |  | 1. init mt5 connection                              |                                      |
|                           |       |  |  | 2. ingest market data                               |                                      |
|                           |       |  |  | 3. Normalize data                                   |                                      |
|---------------------------|       |  |  | 4. cache all symbol data for other process          |───────────┐                          |
|                           |       |  |  |                                                     |           │ all symbols              |                                     
| - bot state               |       |  |  └─────────────────────────────────────────────────────┘           |                          |
| - settings                |───────┘  |                                                                    |                          |
| - process lifecycle       | start 1  |                                      Process2: symbol backtest     ▼                          |
|                           |          |                                      ┌─────────────────────────────────────────────────────┐  |
|                           |          |                                      | params: client, cache_handler                       |  |
|                           | start 2  |                                      |-----------------------------------------------------│  |
|                           |────────────────────────────────────────────────>|    ├── load last backtest timestamp                 |  |
|                           |          |                                      |    │                                                |  |
|                           |          |                                      |    └── every N minutes:                             |  |
|                           |          |                                      |       ├── check if 3 months elapsed                 |  |
|                           |          |                                      |       ├── backtest all symbols                      |  |
|                           |          |                                      |       ├── score symbols                             |  |
|                           |          |                                      |       └── activate top performers                   |  |
|                           |          |                                      |                                                     |  |       
|                           |          |                                      └─────────────────────────────────────────────────────┘  | 
|                           |─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
|                           | start 4  |                                              |                                                |                          |
|                           |          |                                              |                                                |                          |
|                           |          |                                              |   top N symbols                                |                          |
|                           |          |                                              |                                                |                          |
|                           |          |                                              |                                                |                          |
|                           |          |      Process3: Multi strategy thread Handler ▼                                                |                          |
|                           |          |     ┌────────────────────────────────────────────────────────────┐                            |                          |         
|                           |          |     | strategy: MA                                               |                            |                          |
|                           |          |     | └──Thread: EURUSD                                          |                            |                          |        
|                           |          |     |    └── DataHandler(EURUSD_data) > from cache_handler       |                            |                          |
|                           |          |     |            ┌─────────────────────┐                         |                            |                          |
|                           | start 3  |     |            | Symbol Thread       |────────────────────┐    |                            |                          |
|                           |───────────────>|            │---------------------│ MovingAverage      │    |                            |                          |
|                           |          |     |            │ EURUSD              | Crossover Strategy │    |                            |                          |
|                           |          └─────|            │  └── DataHandler    |--------------------│    |────────────────────────────┘                          |
|                           |                |            |    ├── M15          | - indicators       │    |                                                       |
|                           |                |            |    ├── M30          | - alignment        │    |  signal stream 1                                      |
|                           |                |            |    ├── 1H           | - signals          │──────────────────────────┐                                 |
|                           |                |            |    └── 4H           |────────────────────┘    |                     |                                 |
|                           |                |            └─────────────────────┘                         |                     |                                 | 
|                           |                |------------------------------------------------------------|                     |                                 ▼
|                           |                | strategy: scalper                                          |                     |                Process4: trade execution 
|                           |                | └──Thread: GBPUSD                                          |                     |               ┌───────────────────────────────────┐
|                           |                |    └── DataHandler(GBPUSD)                                 |                     └──────────────>|         SIGNAL VALIDATION         |
|                           |                |            ┌─────────────────────┐                         |                                     |       TRADE CONTROL CENTRE        |
|                           |                |            | Symbol Thread       |────────────────────┐    |                                     |-----------------------------------|
|                           |                |            │---------------------│                    │    |                                     |  V  |      TRADE EXECUTION        |
|                           |                |            │ GBPUSD              | Scalper Strategy   │    |                                     |  A  |-----------------------------|
|                           |                |            │  └── DataHandler    |--------------------│    |                                     |  L  |- Risk Management            |
|                           |                |            |    ├── M15          | - indicators       │    |                                     |  I  |- Live Trade Execution       |
|                           |                |            |    ├── M30          | - alignment        │    |   signal stream 2                   |  D  |- Logging + Monitoring       |
|                           |                |            |    ├── 1H           | - signals          │─────────────────────────────────────────>|  A  |          ▼                  |
|                           |                |            |    └── 4H           |────────────────────┘    |                                     |  T  |          ▼                  |
|                           |                |            └─────────────────────┘                         |                                     |  I  |          ▼                  |
|                           |                |------------------------------------------------------------|                                     |  O  |          ▼                  |
|                           |                |strategy: volitility                                        |                                     |  N  |          ▼                  | 
|                           |                |    Thread: USDJPY                                          |                                     |----------------▼------------------|
|                           |                |    └── DataHandler(USDJPY)                                 |                                     |     TRADE LOGGING + MONITORING    |
|                           |                |            ┌─────────────────────┐                         |                                     |-----------------------------------|
|                           |                |            | Symbol Thread       |────────────────────┐    |                 ┌──────────────────>| - Symbol Metrics                  |
|                           |                |            │---------------------│ MovingAverage      │    |                 |                   |   └── score                       |
|                           |                |            │ USDJPY              | Crossover Strategy │    |                 |                   |     ├── health                    |
|                           |                |            │  └── DataHandler    |--------------------│    |                 |                   |     ├── stats                     |
|                           |                |            |    ├── M15          | - indicators       │    |                 |                   |     └── health                    |
|                           |                |            |    ├── M30          | - alignment        │    |                 |                   |                                   |
|                           |                |            |    ├── 1H           | - signals          │──────────────────────┘                   └───────────────────────────────────┘
|                           |                |            |    └── 4H           |────────────────────┘    |   signal stream 3       
|                           |                |            └─────────────────────┘                         |                  
|                           |                |                                                            |                  
|                           |                |                                                            |                  
|                           |                |                                                            |                  
|                           |                └────────────────────────────────────────────────────────────┘
|                           |
└───┬───────────┬───────────┘                     ┌─────────────────────────────────────────────────┐
    |           └────────────────────────────────>|params: data_file_dir, data_plotter, bot_state   |
    |               Process 5:  Dashboard GUI     |-------------------------------------------------|
    |                                             | ┌─────────────────────────────────────────────┐ |
    |                                             | │ Header: Account | Bot Status | Time | User  │ |
/ data \                                          | ├───────────┬─────────────────────────────────┤ |
\stream/                                          | │ Sidebar   │ Main Content Area               │ |
    |                                             | │           │                                 │ |
    |     <symbol/data.json>──────────────────────> │ - Charts  │  (Active Panel)                 │ |
    ├──</process 3>───────────────────────────────> │ - Symbols │                                 │ |
    └──<bot/config.json>──────────────────────────> │ - Bot     │                                 │ |
          <bot/bot_logs.log>──────────────────────> │ - Logs    │                                 │ |
<gitHub.com/repo/releases/v=1.1.2>────────────────> │ - Store   │                                 │ |
                                    ┌─────────────> │ - Support │                                 │ |
                                    |             | └───────────┴─────────────────────────────────┘ |
                                    |             └─────────────────────────────────────────────────┘
                                    ▼
                                muladiDev@iautomate.co.za
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
- Algo tradinig **enabled**

---

## dashboard layout design

```mathematica
┌─────────────────────────────────────────────┐
│ Header: Account | Bot Status | Time | User  │
├───────────┬─────────────────────────────────┤
│ Sidebar   │ Main Content Area               │
│           │                                 │
│ - Charts  │  (Active Panel)                 │
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
