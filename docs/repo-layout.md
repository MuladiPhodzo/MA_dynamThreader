# Project Repository Layout

This is a generated high-level map of the `MovingAverage_Advisor` repository.
It focuses on the main source, runtime, dashboard, and data areas, and omits
cache-heavy or generated internals where they do not add much navigational value.

```text
MovingAverage_Advisor/
|-- dashboard/
|   |-- src/
|   |   |-- app/
|   |   |   |-- api.service.ts
|   |   |   |-- app.component.css
|   |   |   |-- app.component.html
|   |   |   |-- app.component.ts
|   |   |   |-- app.config.ts
|   |   |   `-- models.ts
|   |   |-- assets/
|   |   |   `-- .gitkeep
|   |   |-- index.html
|   |   |-- main.ts
|   |   `-- styles.css
|   |-- angular.json
|   |-- package.json
|   |-- package-lock.json
|   |-- proxy.conf.json
|   |-- start-ui-dev.cmd
|   |-- tsconfig.app.json
|   |-- tsconfig.json
|   `-- tsconfig.spec.json
|
|-- data/
|   `-- _EMA_/
|       |-- AUDCAD/
|       |-- AUDCHF/
|       |-- AUDJPY/
|       |-- AUDNZD/
|       |-- AUDUSD/
|       |-- ...
|       |-- EURUSD/
|       |-- GBPUSD/
|       |-- USDCHF/
|       `-- USDJPY/
|
|-- docs/
|   `-- repo-layout.md
|
|-- runtime/
|   |-- cache/
|   |   |-- AUDCAD.json
|   |   |-- AUDCHF.json
|   |   |-- ...
|   |   `-- USDZAR.json
|   |-- flow_state.json
|   `-- supervisor_state.json
|
|-- src/
|   |-- main/
|   |   |-- python/
|   |   |   |-- advisor/
|   |   |   |   |-- api/
|   |   |   |   |   |-- server.py
|   |   |   |   |   `-- __init__.py
|   |   |   |   |
|   |   |   |   |-- backtest/
|   |   |   |   |   |-- core.py
|   |   |   |   |   |-- engine.py
|   |   |   |   |   |-- metrics.py
|   |   |   |   |   `-- __init__.py
|   |   |   |   |
|   |   |   |   |-- bootstrap/
|   |   |   |   |   |-- config_loader.py
|   |   |   |   |   |-- state_loader.py
|   |   |   |   |   `-- sys_bootstrap.py
|   |   |   |   |
|   |   |   |   |-- Client/
|   |   |   |   |   |-- mt5Client.py
|   |   |   |   |   |-- __init__.py
|   |   |   |   |   `-- symbols/
|   |   |   |   |       |-- symbol_watch.py
|   |   |   |   |       `-- __init__.py
|   |   |   |   |
|   |   |   |   |-- core/
|   |   |   |   |   |-- config_watcher.py
|   |   |   |   |   |-- dependency_graph.py
|   |   |   |   |   |-- event_bus.py
|   |   |   |   |   |-- events.py
|   |   |   |   |   |-- flow_state.py
|   |   |   |   |   |-- health_bus.py
|   |   |   |   |   |-- locks.py
|   |   |   |   |   |-- rate_limiter.py
|   |   |   |   |   |-- restart_store.py
|   |   |   |   |   |-- state.py
|   |   |   |   |   `-- __init__.py
|   |   |   |   |
|   |   |   |   |-- GUI/
|   |   |   |   |   |-- userInput.py
|   |   |   |   |   `-- __init__.py
|   |   |   |   |
|   |   |   |   |-- Logs/
|   |   |   |   |   `-- <per-symbol backtest CSV/TXT outputs>
|   |   |   |   |
|   |   |   |   |-- mt5_pipeline/
|   |   |   |   |   |-- core.py
|   |   |   |   |   |-- runner.py
|   |   |   |   |   `-- __init__.py
|   |   |   |   |
|   |   |   |   |-- process/
|   |   |   |   |   |-- heartbeats.py
|   |   |   |   |   |-- process_engine.py
|   |   |   |   |   `-- __init__.py
|   |   |   |   |
|   |   |   |   |-- scheduler/
|   |   |   |   |   |-- process_sceduler.py
|   |   |   |   |   |-- readiness_gate.py
|   |   |   |   |   |-- requirements.py
|   |   |   |   |   |-- resource_registry.py
|   |   |   |   |   `-- resources.py
|   |   |   |   |
|   |   |   |   |-- Strategy_model/
|   |   |   |   |   |-- backtest_engine.py
|   |   |   |   |   |-- strategy.py
|   |   |   |   |   |-- strategy_runner.py
|   |   |   |   |   |-- Fundamentals/
|   |   |   |   |   |   |-- Tools
|   |   |   |   |   |   |   |-- __init__.py
|   |   |   |   |   |   |   |-- fair_value_gap.py
|   |   |   |   |   |   |   |-- market_structure.py
|   |   |   |   |   |   |   `-- order_blocks.py
|   |   |   |   |   |   |-- __init__.py
|   |   |   |   |   |   |-- technical_base.py
|   |   |   |   |   |   |-- technical_registry.py
|   |   |   |   |   |-- indicators/
|   |   |   |   |   |   |-- ATR/
|   |   |   |   |   |   |   `-- ATR.py
|   |   |   |   |   |   |-- Awesome_Ascillator/
|   |   |   |   |   |   |   `-- awesome_ascillator.py
|   |   |   |   |   |   |-- MA/
|   |   |   |   |   |   |   `-- MovingAverage.py
|   |   |   |   |   |   |-- MACD/
|   |   |   |   |   |   |   `-- MACD.py
|   |   |   |   |   |   |-- indicator_base.py
|   |   |   |   |   |   `-- __init__.py
|   |   |   |   |   `-- signals/
|   |   |   |   |       |-- decider.py
|   |   |   |   |       |-- filters.py
|   |   |   |   |       |-- score_engine.py
|   |   |   |   |       |-- signal_store.py
|   |   |   |   |       `-- __init__.py
|   |   |   |   |
|   |   |   |   |-- Telegram/
|   |   |   |   |   |-- core.py
|   |   |   |   |   |-- runner.py
|   |   |   |   |   |-- __init__.py
|   |   |   |   |   `-- utils/
|   |   |   |   |       |-- env_loader.py
|   |   |   |   |       |-- logger.py
|   |   |   |   |       |-- singleton.py
|   |   |   |   |       `-- __init__.py
|   |   |   |   |
|   |   |   |   |-- Trade/
|   |   |   |   |   |-- RiskManager.py
|   |   |   |   |   |-- tradeHandler.py
|   |   |   |   |   |-- tradeStats.py
|   |   |   |   |   |-- trade_engine.py
|   |   |   |   |   |-- trateState.py
|   |   |   |   |   `-- __init__.py
|   |   |   |   |
|   |   |   |   |-- utils/
|   |   |   |   |   |-- cache_handler.py
|   |   |   |   |   |-- config_handler.py
|   |   |   |   |   |-- dataHandler.py
|   |   |   |   |   |-- date_utils.py
|   |   |   |   |   |-- error_handling.py
|   |   |   |   |   |-- locks.py
|   |   |   |   |   |-- logging_setup.py
|   |   |   |   |   |-- math_utils.py
|   |   |   |   |   `-- __init__.py
|   |   |   |   |
|   |   |   |   |-- MA_DynamAdvisor.py
|   |   |   |   |-- __main__.py
|   |   |   |   `-- __init__.py
|   |   |   `-- __init__.py
|   |   `-- scripts/
|   |
|   `-- tests/
|       |-- python/
|       |   |-- configTests.py
|       |   |-- test_Advisor.py
|       |   |-- test_api_integration.py
|       |   |-- test_ma_dynamadvisor.py
|       |   |-- test_MovingAverages.py
|       |   |-- test_mt5_client.py
|       |   |-- test_pipeline_polling.py
|       |   |-- test_processes.py
|       |   |-- test_system_integration.py
|       |   |-- test_system_program.py
|       |   |-- test_telegram.py
|       |   `-- __init__.py
|       `-- __init__.py
|
|-- .env
|-- .flake8
|-- .gitignore
|-- bot_state.json
|-- build.py
|-- configs.json
|-- DockerFile
|-- LICENSE.txt
|-- makefile
|-- MA_DynamAdvisor.log
|-- pyproject.toml
|-- README.md
|-- requirements.txt
|-- setup.py
`-- __main__.spec
```

## Notes

- Generated and cache-heavy directories exist in the repo, including
  `.git/`, `.pytest_cache/`, `.pybuilder/`, `build/`, `target/`, `venv/`,
  `dashboard/dist/`, and `dashboard/.angular/`.
- The `data/`, `runtime/cache/`, and `src/main/python/advisor/Logs/` areas are
  large and symbol-driven, so they are summarized rather than listed in full.
