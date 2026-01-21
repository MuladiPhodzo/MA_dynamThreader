# 📈 MovingAverage Advisor Bot

```markdown

The **MovingAverage Advisor** is an automated trading bot designed to analyze market trends using multi-
timeframe moving average crossover strategies. It connects 
to **MetaTrader 5 (MT5)** and makes buy/sell decisions based on 
real-time price data and calculated signals.

---

## High-Level Layer Overview

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

```

MovingAverage_Advisor/
├── advisor/                    # Core logic
|   ├── Client/
│   |   ├── __init__.py
|   │   └── mt5Client.py
|   ├── GUI/
│   |   ├── __init__.py
|   │   └── userInput.py
│   ├── Trade/
│   |   ├── __init__.py
│   |   ├── statsManager.py
│   │   └── TradesAlgo.py      # Trade execution logic
│   ├── database/
│   |   ├── __init__.py
│   |   └── MySQLdatabase.py   # Optional DB logging
|   ├── Telegram/
│   |   └── __init__.py
│   |       ├── core.py              # main TelegramMessenger (async)
│   |       ├── runner.py            # bot startup with lock + watchdog
│   |       ├── handlers/
│   |       │   ├── __init__.py
│   |       │   ├── start_handler.py
│   |       │   ├── stop_handler.py
│   |       │   ├── status_handler.py
│   |       └── utils/
│   |           ├── logger.py        # rotating logs
│   |           ├── singleton.py     # PID lock + stale cleanup
│   |           ├── env_loader.py    # unified .env resolver
│   |           └── healthcheck.py   # simple health server
|   ├── utils/
|   │   ├── __init__.py
│   │   ├── cache.py
│   │   ├── ThreadHandler.py
│   |   └── dataHandler.py  
|   │
|   ├── MovingAverage/
│   |   ├── __init__.py
|   │   └── MovingAverage.py       # Strategy implementation
|   │
│   └── MA_DynamAdvisor.py       # Main bot runner
├── .env
├── makefile
├── build.py                   # PyBuilder build script
├── Dockerfile                 # Docker container config
├── requirements.txt           # Python dependencies
├── README.md                  # Project documentation
└── .pybuilder/                # PyBuilder generated files

```bash

## 🚀 Getting Started

### 🧰 Prerequisites

- Python 3.10+
- MetaTrader5 terminal installed and configured
- Docker (optional for containerization)

### 🔧 Installation (Local)

```bash
# Clone the repository
git clone https://github.com/MuladiPhodzo/MovingAverage_Advisor.git
cd MovingAverage_Advisor

# Install dependencies
pip install -r requirements.txt

# Build the project (optional)
pyb clean install
```

## 🐳 Running with Docker

### Make sure Docker is installed and running

1. __Build the image:__

```bash
docker build -t movingaverage-advisor .
```

1. __Run the container:__

```bash
docker run -it --rm movingaverage-advisor
```

---

## ⚙️ Configuration

Symbols and timeframes are managed inside the `Advisor` module. Example usage in `RunAdvisorBot.py`:

```python
bot.main("EURUSD", bot.advisor, bot.advisor.TF)
```

You can add multiple symbols and timeframes by editing the `TF` and `symbols` properties.

---

## 🧪 Testing

Unit tests are located in:

```bash
src/unittest/python/
```

To run tests via PyBuilder:

```bash
pyb run_unit_tests
```

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

__Phodzo Lionel Muladi__  
Email: <muladi.lionel@gmail.com>  
LinkedIn: <https://www.linkedin.com/in/phodzo-muladi-654214257>

---

## 📜 License

This project is licensed under the MIT License
This project is licensed under the MIT License
