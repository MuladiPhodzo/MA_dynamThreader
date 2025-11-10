```markdown
# 📈 MovingAverage Advisor Bot

The **MovingAverage Advisor** is an automated trading bot designed to analyze market trends using multi-timeframe moving average crossover strategies. It connects to **MetaTrader 5 (MT5)** and makes buy/sell decisions based on real-time price data and calculated signals.

---

## 🧠 Key Features

- Multi-timeframe (HTF/LTF) strategy support
- Moving Average crossover-based signal generation
- Automated decision-making and trade execution
- Threaded execution for handling multiple symbols concurrently
- Customizable trade thresholds and timeframes
- Modular and extensible Python codebase

---

## 📁 Project Structure

```
MovingAverage_Advisor/
│
├── advisor/                    # Core logic
│   ├── Advisor.py             # MetaTrader5 client and data handler
│   ├── RunAdvisorBot.py       # Main bot runner
│   ├── Trade/
│   │   └── TradesAlgo.py      # Trade execution logic
│   ├── database/
│   |   ├── MySQLdatabase.py   # Optional DB logging
|   ├── Telegram/
│   |   ├── __init__.py
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
|   │
|   ├── MovingAverage/
|   │   └── MovingAverage.py       # Strategy implementation
|   │
├── .env
├── makefile
├── build.py                   # PyBuilder build script
├── Dockerfile                 # Docker container config
├── requirements.txt           # Python dependencies
├── README.md                  # Project documentation
└── .pybuilder/                # PyBuilder generated files

```

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


### 🐳 Running with Docker

1. **Make sure Docker is installed and running**

2. **Build the image:**

```bash
docker build -t movingaverage-advisor .
```

3. **Run the container:**

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

```
src/unittest/python/
```

To run tests via PyBuilder:

```bash
pyb run_unit_tests
```

---

## 📝 Todo

- Integrate database logging (MySQL/PostgreSQL)
- Add support for alternative strategies
- Implement backtesting module

---

## 🧠 Author

**Phodzo Lionel Muladi**  
Email: muladi.lionel@gmail.com  
LinkedIn: https://www.linkedin.com/in/phodzo-muladi-654214257

---

## 📜 License

This project is licensed under the MIT License
```
