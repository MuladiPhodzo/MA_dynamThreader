# MA_DynamAdvisor.py  -- INTEGRATED with ThreadHandler
import sys
import time
import threading
import logging

import traceback
from typing import Callable, Dict, List, Optional

# -------------------------
# Logging Configuration
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("MA_DynamAdvisor.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)


# -------------------------
# ThreadHandler & ManagedThread (integrated)
# -------------------------
class ManagedThread:
    """
    Wrapper object for managing individual threads with pause & stop controls,
    auto-restart, callbacks and basic metrics.
    """
    def __init__(
        self,
        name: str,
        group: str,
        ttype: str,
        target: Callable,
        args: tuple = (),
        auto_restart: bool = False,
        max_restarts: int = 3,
        callbacks: dict = None,
        logger: Optional[Callable] = None,
    ):
        self.name = name
        self.group = group
        self.type = ttype
        self.target = target
        self.args = args or ()
        self.logger = logger or (lambda m: logger.info(m))

        self.pause_event = threading.Event()
        self.stop_event = threading.Event()
        self.pause_event.set()  # unpaused by default

        self.auto_restart = auto_restart
        self.max_restarts = max_restarts
        self.restart_count = 0

        self.callbacks = callbacks or {}

        # metrics
        self.start_time = None
        self.cycles = 0
        self.total_runtime = 0.0

        self.thread = threading.Thread(target=self._run_wrapper, name=name, daemon=True)

    def _log(self, msg: str):
        try:
            self.logger(msg)
        except Exception:
            logger.info(msg)

    def _apply_cb(self, event: str):
        try:
            cb = self.callbacks.get(event)
            if callable(cb):
                cb(self)
        except Exception:
            self._log(f"[CallbackError] {self.name} - {event}\n" + traceback.format_exc())

    def _run_wrapper(self):
        while not self.stop_event.is_set():
            try:
                self._apply_cb("on_start")
                self.start_time = time.time()
                self._log(f"[ThreadHandler] Thread '{self.name}' started.")

                # The target is expected to accept stop_event and pause_event as kwargs.
                # We'll call it and assume it either loops internally or returns (one-shot).
                while not self.stop_event.is_set():
                    # If paused at ManagedThread level, this will block here.
                    self.pause_event.wait()

                    cycle_start = time.time()

                    # call target; target must accept stop_event and pause_event kwargs.
                    # We pass them as keywords so legacy functions can ignore them if not used.
                    try:
                        self.target(*self.args, stop_event=self.stop_event, pause_event=self.pause_event)
                    except TypeError:
                        # fallback: call without keyword args for backward compatibility
                        self.target(*self.args)

                    # metrics
                    self.cycles += 1
                    self.total_runtime += time.time() - cycle_start

                    # If target is single-shot, break here so top-level loop can handle restart/exit
                    # We'll break and let the outer try/except determine restarting behavior.
                    break

            except Exception as e:
                self._log(f"[ThreadHandler] ERROR in '{self.name}': {e}")
                self._log(traceback.format_exc())
                self._apply_cb("on_error")

                if not self.auto_restart:
                    break

                self.restart_count += 1
                if self.restart_count > self.max_restarts:
                    self._log(f"[ThreadHandler] Max restarts exceeded for '{self.name}'. Stopping.")
                    break

                self._log(f"[ThreadHandler] Restarting '{self.name}' ({self.restart_count}/{self.max_restarts})...")
                time.sleep(1)
                continue

            # finished cleanly
            break

        self._apply_cb("on_stop")
        self._log(f"[ThreadHandler] Thread '{self.name}' stopped.")


class ThreadHandler:
    """
    Manages multiple ManagedThread instances, exposes start/pause/resume/stop/fetch operations.
    """
    def __init__(self, logger: Optional[Callable] = None):
        self.threads: Dict[str, ManagedThread] = {}
        self.logger = logger or (lambda m: logger.info(m))

    def _log(self, msg: str):
        try:
            self.logger(msg)
        except Exception:
            logger.info(msg)

    def start_thread(
        self,
        name: str,
        group: str,
        ttype: str,
        target: Callable,
        args: tuple = (),
        auto_restart: bool = False,
        max_restarts: int = 3,
        callbacks: dict = None,
    ):
        if name in self.threads:
            self._log(f"[ThreadHandler] Thread '{name}' already exists; attempting to resume or restart.")
            mt = self.threads[name]
            # If previously stopped, create a new Thread object
            mt.pause_event.set()
            mt.stop_event.clear()
            if not mt.thread.is_alive():
                mt.thread = threading.Thread(target=mt._run_wrapper, name=name, daemon=True)
                mt.thread.start()
            return

        mt = ManagedThread(
            name=name,
            group=group,
            ttype=ttype,
            target=target,
            args=args,
            auto_restart=auto_restart,
            max_restarts=max_restarts,
            callbacks=callbacks,
            logger=self.logger,
        )
        self.threads[name] = mt
        mt.thread.start()
        self._log(f"[ThreadHandler] Created and started '{name}' ({group}/{ttype}).")

    def pause_thread(self, name: str):
        mt = self.threads.get(name)
        if mt:
            mt.pause_event.clear()
            mt._apply_cb("on_pause")
            self._log(f"[ThreadHandler] Paused '{name}'.")

    def resume_thread(self, name: str):
        mt = self.threads.get(name)
        if mt:
            mt.pause_event.set()
            mt._apply_cb("on_resume")
            self._log(f"[ThreadHandler] Resumed '{name}'.")

    def stop_thread(self, name: str):
        mt = self.threads.get(name)
        if mt:
            mt.stop_event.set()
            self._log(f"[ThreadHandler] Stopping '{name}'.")

    def stop_group(self, group: str):
        for mt in self.threads.values():
            if mt.group == group:
                mt.stop_event.set()
                self._log(f"[ThreadHandler] Stopping group thread '{mt.name}'.")

    def stop_type(self, ttype: str):
        for mt in self.threads.values():
            if mt.type == ttype:
                mt.stop_event.set()
                self._log(f"[ThreadHandler] Stopping type thread '{mt.name}'.")

    def stop_all(self):
        for mt in self.threads.values():
            mt.stop_event.set()
        self._log("[ThreadHandler] Stopping ALL threads.")

    def get_by_group(self, group: str) -> List[ManagedThread]:
        return [t for t in self.threads.values() if t.group == group]

    def get_by_type(self, ttype: str) -> List[ManagedThread]:
        return [t for t in self.threads.values() if t.type == ttype]

    def get_by_name(self, name: str) -> Optional[ManagedThread]:
        return self.threads.get(name)

    def thread_stats(self, name: str):
        t = self.threads.get(name)
        if not t:
            return None
        return {
            "name": t.name,
            "group": t.group,
            "type": t.type,
            "cycles": t.cycles,
            "avg_runtime": (t.total_runtime / t.cycles) if t.cycles else 0,
            "is_alive": t.thread.is_alive(),
            "restarts": t.restart_count,
        }

    def wait_for_all(self, timeout: Optional[float] = None):
        """Wait for all threads to finish. timeout is total seconds to wait (approx)."""
        start = time.time()
        while any(mt.thread.is_alive() for mt in self.threads.values()):
            if timeout is not None and (time.time() - start) >= timeout:
                break
            time.sleep(0.1)
