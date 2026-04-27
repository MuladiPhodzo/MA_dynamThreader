# StrategyManager Audit

Scope: `src/main/python/advisor/Strategy_model/strategy_runner.py`

## Role

`StrategyManager` is the live strategy orchestration layer. It is registered as
an event-driven process from `MA_DynamAdvisor.py`, subscribes to market-data
events, attaches strategies to symbols, runs strategy callables, normalizes
their outputs, stores generated signals, and publishes signal events.

It does not own trade execution. It does not currently own backtest execution,
although it can emit backtest request events when it attaches a strategy.

## Runtime Wiring

```text
MA_DynamAdvisor._init_core_instances()
  -> StrategyManager(...)
  -> Supervisor.register_process(
       name="strategy",
       target=self.strategy,
       depends=["pipeline"],
       event_driven=True,
     )
  -> Supervisor.register_process(
       name="execution",
       target=self.execution,
       depends=["strategy"],
       event_driven=True,
     )
```

Because it is event-driven, the supervisor does not spawn a thread for
`StrategyManager`. `register()` is called during process registration and the
manager lives through event bus subscriptions.

## Call Chain

Registration:

```text
StrategyManager.register()
  -> subscribe("market_data_ready", _on_symbols)
  -> subscribe("strategy_config_updated", _on_strategy_catalog_update)
  -> subscribe("market_data_ready:<symbol>") for each known symbol
  -> health phase: registered
```

Symbol event subscription:

```text
market_data_ready
  -> _on_symbols()
  -> _subscribe_symbol(symbol)
  -> subscribe("market_data_ready:<symbol>")
```

Live strategy execution:

```text
market_data_ready:<symbol>
  -> _spawn_market_data_task()
  -> asyncio.create_task(_on_market_data(symbol))
  -> _on_market_data()
     -> skip if shutdown
     -> skip if symbol already running
     -> lookup SymbolState
     -> create default StrategyModel if no strategies are attached
     -> scheduler.schedule("strategy:<symbol>", task=_run_symbol)
  -> _run_symbol()
     -> skip if symbol disabled
     -> skip if cache/telemetry not ready
     -> for each attached strategy:
        -> _build_signal()
        -> _invoke_strategy()
        -> _normalize_signal()
        -> StrategyRegistry.record_signal()
        -> _publish_signal()
```

Signal publication:

```text
_publish_signal()
  -> SignalStore.add_signal(payload)
  -> SymbolWatch.mark_signal(symbol)
  -> publish("signal_generated")
  -> publish("signal_generated:<symbol>")
```

Execution process consumes these events separately and decides whether to place
orders.

## Findings

### 1. Strategy task creation is fire-and-forget

`_spawn_market_data_task()` creates an asyncio task and only logs failures in
the done callback. This keeps events responsive, but it means API/status callers
cannot observe queue depth, skipped events, or pending strategy work directly.

Impact:

- The strategy panel can report the service as running while per-symbol tasks
  are failing, skipped, or waiting.
- Burst market-data events can create many background tasks, bounded only later
  by `_running` and scheduler limits.

Recommendation:

- Track counters for `events_received`, `tasks_started`, `tasks_skipped_busy`,
  `tasks_failed`, and `last_task_error`.
- Expose those counters in `health_bus.update("strategy", ...)`.

### 2. Disabled symbols silently prevent signals

`_run_symbol()` returns immediately when the symbol is missing or disabled.
That is correct behavior, but it is operationally quiet.

Impact:

- A running strategy service with all symbols disabled produces no signals and
  can look broken.
- The health payload only shows service phase/running/subscribed_symbols unless
  a symbol task reaches later update logic.

Recommendation:

- Mark per-symbol health or telemetry when a run is skipped because the symbol
  is disabled.
- Add dashboard copy/status for "subscribed but disabled" versus "actively
  evaluating".

### 3. Backtest request emission is currently misleading

`create_symbol_strategy()` emits `RUN_BACKTEST:<strategy_name>` by default after
attaching a strategy. The main runtime currently comments out `BacktestProcess`
registration in `MA_DynamAdvisor.py`, so those events may have no consumer.

Impact:

- The code suggests strategy creation triggers backtests, but the runtime may
  drop those events.
- A future re-enable of `BacktestProcess` could unexpectedly start backtests
  when live market events lazily attach strategies.

Recommendation:

- Default `emit_backtest_on_create` to `False` for live lazy attachment, or make
  it explicitly controlled by config.
- Re-enable and test `BacktestProcess`, or remove this emission path from
  `StrategyManager` and keep backtest triggering in the API/backtest layer.

### 4. Async strategies are rejected inside an active event loop

`_invoke_strategy()` raises `RuntimeError("Async strategies must be executed via
the scheduler")` if a strategy returns an awaitable while an event loop is
running. In this path, `_build_signal()` is already called inside
`asyncio.to_thread()`, so normal strategy callables are safe, but an async
callable returning a coroutine will fail rather than being awaited.

Impact:

- Future async strategies can appear to run but never emit signals.
- The error is recorded as a strategy error, but the failure mode is not
  obvious from the UI.

Recommendation:

- Decide whether strategies are sync-only.
- If async strategies are supported, move invocation into an async path and
  await results explicitly.

### 5. `OrchestratedStrategy` fallback path is mostly unreachable

`_on_market_data()` creates a default strategy whenever a symbol has no
strategies. `_run_symbol()` only uses `_build_orchestrated_signal()` when the
symbol has no strategies. In the normal event path, that fallback is bypassed
because the strategy is created first.

Impact:

- Tests cover `_build_orchestrated_signal()`, but production will mostly use
  attached `StrategyModel` instances.
- The fallback can drift from the real live path.

Recommendation:

- Remove the fallback if no longer needed, or make it a deliberate mode with a
  named configuration flag.

### 6. Strategy registry refresh is config-only

`_on_strategy_catalog_update()` refreshes `StrategyRegistry` configs, but it
does not reattach or rebuild already-created per-symbol strategy instances.

Impact:

- Creating/updating a strategy config may update the registry while existing
  symbol strategies continue running their old in-memory config.

Recommendation:

- On config update, either clear attached strategies for affected symbols or
  rebuild strategy instances in place.
- Emit a health event showing how many symbols/strategies were refreshed.

### 7. Health is service-level, not task-level enough

`_mark_service_health()` records phase, number of running symbols, and subscribed
symbols. Per-symbol health is only updated at the end of `_run_symbol()` when
the function reaches that point.

Impact:

- Early returns for disabled symbols, missing state, or warmup do not surface
  clearly.
- "Strategy running" can mean registered/subscribed, not necessarily producing
  evaluations.

Recommendation:

- Add structured skip reasons:
  - `disabled`
  - `cache_not_ready`
  - `no_symbol_state`
  - `already_running`
  - `no_signal`
  - `signal_generated`

### 8. `_running` is an in-memory set without a lock

All current access should happen on the event loop thread, so this is probably
safe today. The manager also uses callbacks and scheduler work that touch other
threaded paths, so this deserves a guard if the execution model changes.

Impact:

- Low current risk.
- Medium future risk if event handlers start calling back across threads.

Recommendation:

- Keep all `_running` mutations on the event loop, or wrap it with a small
  async lock if cross-thread access appears.

### 9. Signal normalization is permissive but opaque

`_normalize_signal()` accepts `side`, `direction`, or `sig`, filters weak text,
and supplies default SL/TP when missing. This makes strategy outputs tolerant,
but it can hide malformed strategy payloads.

Impact:

- A strategy missing SL/TP can still trade with default risk distances.
- A strategy with ambiguous side text simply returns no signal.

Recommendation:

- Record normalization drop reasons in the registry or health payload.
- Add a strict mode for live trading that requires explicit side, SL, and TP.

## Test Coverage

Existing tests cover:

- Bullish signal normalization.
- Weak signal filtering.
- Fallback/orchestrated signal smoke path.
- Market-data to execution integration.

Missing tests worth adding:

- Disabled symbol produces no signal and records a skip reason.
- Cache-not-ready produces no signal and records warmup health.
- Strategy config update rebuilds or intentionally does not rebuild attached
  strategies.
- `emit_backtest_on_create` behavior when `BacktestProcess` is not registered.
- Async strategy callable behavior.
- Duplicate/busy symbol event skip behavior.

## Recommended Next Patch

Highest leverage changes:

1. Add strategy health counters and skip reasons.
2. Make live lazy strategy attachment stop emitting backtest requests by default.
3. Add tests for disabled/cache-missing/config-refresh behavior.
4. Add dashboard status fields that distinguish subscribed, evaluating,
   skipped, and signal-producing strategy states.
