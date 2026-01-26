# 📈 MovingAverage Advisor Bot

```markdown

The **MovingAverage Advisor** is an automated trading bot designed to analyze market trends using 
multi-timeframe moving average crossover strategies. It connects to **MetaTrader 5 (MT5)** and 
makes buy/sell decisions based on real-time price data and 
calculated signals.

---

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
                                                                 Shared Cache (Authoritative)
Main                                   ┌───────────────────────────────────────────────────────────────────────────────────────────────┐
┌───────────────────────────┐          |                                                                                               |
|                           |          |  Process1: mt5 pipeline                                                                       |
| Process                   |          |  ┌─────────────────────────────────────────────────────┐                                      |
| Supervisor                |       ┌────>|                                                     |                                      |
|                           |       |  |  │-----------------------------------------------------│                                      |
|                           |       |  |  | 1. Discover symbols                                 |                                      |
|                           |       |  |  | 2. Backtest each symbol (Pooled )                   |                                      |
|                           |       |  |  | 3. Score performance                                | all symbols                          |
|---------------------------|       |  |  | 4. Rank symbols                                     |───────────┐                          |
|                           |       |  |  | 5. Select top N symbols                             |           │                          |                                     
| - bot state               |       |  |  └─────────────────────────────────────────────────────┘           |                          |
| - settings                |───────┘  |                                                                    |                          |
| - process lifecycle       | start 1  |                                      Process2: symbol backtest     ▼                          |
|                           |          |                                      ┌─────────────────────────────────────────────────────┐  |
|                           |          |                                      |                                                     |  |
|                           | start 2  |                                      |-----------------------------------------------------│  |
|                           |────────────────────────────────────────────────>|    ├── load last backtest timestamp                 |  |
|                           |          |                                      |    │                                                |  |
|                           |          |                                      |    └── every N minutes:                             |  |
|                           |          |                                      |       ├── check if 3 months elapsed                 |  |
|                           |          |                                      |       ├── backtest all symbols                      |  |
|                           |          |                                      |       ├── rank symbols                              |  |
|                           |          |                                      |       └── activate top performers                   |  |
|                           |          |                                      |                                                     |  |       
|                           |          |                                      └─────────────────────────────────────────────────────┘  | 
|                           |─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
|                           | start 4  |                                              |                                                |                          |
|                           |          |                                              |                                                |                          |
|                           |          |                                              |   top N symbols                                |                          |
|                           |          |                                              |                                                |                          |
|                           |          |                                              |                                                |                          |
|                           |          |      Process3: thread Handler                ▼                                                |                          |
|                           |          |     ┌────────────────────────────────────────────────────────────┐                            |                          |         
|                           |          |     |    Thread: EURUSD                                          |                            |                          |        
|                           |          |     |    └── DataHandler(EURUSD)                                 |                            |                          |
|                           |          |     |            ┌─────────────────────┐                         |                            |                          |
|                           | start 3  |     |            | Symbol Thread       |────────────────────┐    |                            |                          |
|                           |───────────────>|            │---------------------│ MovingAverage      │    |                            |                          |                               
|                           |          |     |            │ EURUSD              | Crossover Strategy │    |                            |                          |
|                           |          └─────|            │  └── DataHandler    |--------------------│    |────────────────────────────┘                          |
|                           |                |            |    ├── M15          | - indicators       │    |                                                       |
|                           |                |            |    ├── M30          | - alignment        │    |  signal1                                              |
|                           |                |            |    ├── 1H           | - signals          │────|─────────────────────┐                                 |
|                           |                |            |    └── 4H           |────────────────────┘    |                     |                                 |
|                           |                |            └─────────────────────┘                         |                     |                                 ▼ 
|                           |                |                                                            |                     |                Process4: trade execution 
|                           |                |    Thread: GBPUSD                                          |                     |               ┌───────────────────────────────────┐
|                           |                |    └── DataHandler(GBPUSD)                                 |                     └──────────────>|                                   |
|                           |                |            ┌─────────────────────┐                         |                                     |                                   |
|                           |                |            | Symbol Thread       |────────────────────┐    |                                     |-----------------------------------|
|                           |                |            │---------------------│ MovingAverage      │    |                                     | - Risk Management                 |
|                           |                |            │ GBPUSD              | Crossover Strategy │    |                                     |                                   |
|                           |                |            │  └── DataHandler    |--------------------│    |                                     | - Live Trade Execution            |
|                           |                |            |    ├── M15          | - indicators       │    |                                     |                                   |
|                           |                |            |    ├── M30          | - alignment        │    |   signal 2                          | - Logging + Monitoring            |
|                           |                |            |    ├── 1H           | - signals          │────|────────────────────────────────────>|                                   |
|                           |                |            |    └── 4H           |────────────────────┘    |                                     |                                   |
|                           |                |            └─────────────────────┘                         |                                     |                                   |
|                           |                |                                                            |                                     |                                   |
|                           |                |    Thread: GBPUSD                                          |                                     |                                   |
|                           |                |    └── DataHandler(GBPUSD)                                 |                                     |                                   |
|                           |                |            ┌─────────────────────┐                         |                                     |                                   |
|                           |                |            | Symbol Thread       |────────────────────┐    |                 ┌──────────────────>|                                   |
|                           |                |            │---------------------│ MovingAverage      │    |                 |                   |                                   |
|                           |                |            │ GBPUSD              | Crossover Strategy │    |                 |                   |                                   |
|                           |                |            │  └── DataHandler    |--------------------│    |                 |                   └───────────────────────────────────┘
|                           |                |            |    ├── M15          | - indicators       │    |                 |       
|                           |                |            |    ├── M30          | - alignment        │    |                 |       
|                           |                |            |    ├── 1H           | - signals          │────|─────────────────┘       
|                           |                |            |    └── 4H           |────────────────────┘    |   signal 3       
|                           |                |            └─────────────────────┘                         |                  
|                           |                |                                                            |                  
|                           |                |                                                            |                  
|                           |                |                                                            |                  
|                           |                └────────────────────────────────────────────────────────────┘
|                           |
└───────────────────────────┘
```

## 🚀 Getting Started

### 🧰 Prerequisites

---

## Dev-requirements

- Python **3.10+**
- MetaTrader5 terminal installed and configured
- Docker

---

---

## client rewuirements

- MetaTrader5 terminal **installed**
- good **connectivity**
- Algo tradinig **enabled**

---

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
