# core/locks.py
from filelock import FileLock

STATE_LOCK = FileLock("bot_state.lock")
