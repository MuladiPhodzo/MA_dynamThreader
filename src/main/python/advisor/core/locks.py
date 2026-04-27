# core/locks.py
from filelock import FileLock

STATE_LOCK = FileLock("bot_state.lock")
CONFIG_LOCK = FileLock("configs.lock")
STRATEGY_REGISTRY_LOCK = FileLock("strategy_registry.lock")
