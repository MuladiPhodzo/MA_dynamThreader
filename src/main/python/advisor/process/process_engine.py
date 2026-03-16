import json
import os
import logging
import signal
import sys
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from multiprocessing import Manager
from pathlib import Path

from advisor.core import dependency_graph, health_bus
from advisor.core.state import BotLifecycle, StateManager
from advisor.process.heartbeats import HeartbeatRegistry
from advisor.scheduler.resource_registry import ResourceRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("MA_DynamAdvisor.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("Orchestrator")


@dataclass
class ManagedProcess:
    name: str
    target: callable
    args: tuple = field(default_factory=tuple)
    dependencies: list[str] = field(default_factory=list)
    process: threading.Thread | None = None
    restart_count: int = 0


class Supervisor:
    STATE_FILE = Path("runtime/supervisor_state.json")
    MAX_RESTARTS = 5
    HEARTBEAT_TIMEOUT = timedelta(minutes=1)
    STATUS_LOG_INTERVAL = timedelta(seconds=30)

    def __init__(self, shutdown, state_manager: StateManager, heartbeats: HeartbeatRegistry):
        self.shutdown = shutdown
        self.manager = Manager()
        self.state_manager = state_manager

        self.registry = ResourceRegistry(self.manager)
        self.health_bus = health_bus.HealthBus(self.manager)
        self.heartbeats = heartbeats.beats

        self.dep_graph = dependency_graph.DependencyGraph()
        self.processes: dict[str, ManagedProcess] = {}
        self.restart_counts: dict[str, int] = {}
        self.last_backtest = state_manager.last_backtest_run

        self._load_state()
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _load_state(self) -> None:
        self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not self.STATE_FILE.exists():
            return
        try:
            raw = json.loads(self.STATE_FILE.read_text(encoding="utf-8"))
            self.restart_counts = raw.get("restart_counts", {})
            ts = raw.get("last_backtest")
            if ts:
                self.last_backtest = datetime.fromisoformat(ts)
        except Exception:
            logger.exception("Failed to load supervisor state")

    def _persist_state(self) -> None:
        payload = {
            "restart_counts": self.restart_counts,
            "last_backtest": self.last_backtest.isoformat() if self.last_backtest else None,
        }
        tmp = self.STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.STATE_FILE)

    def register_process(
        self,
        name: str,
        target,
        *args,
        depends: list[str] | None = None,
    ) -> None:
        proc = ManagedProcess(
            name=name,
            target=target,
            args=args,
            dependencies=depends or [],
        )
        self.processes[name] = proc
        self.restart_counts.setdefault(name, 0)
        self.dep_graph.add(name, depends or [])
        logger.info("Registered process: %s (depends on: %s)", name, depends or [])

    def get_process_snapshot(self) -> dict[str, dict]:
        snapshot = {}
        for name, proc in self.processes.items():
            is_alive = bool(proc.process and proc.process.is_alive())
            thread_id = None
            if is_alive and proc.process:
                thread_id = proc.process.native_id if hasattr(proc.process, "native_id") else proc.process.ident
            snapshot[name] = {
                "running": is_alive,
                "pid": thread_id,
                "process_pid": os.getpid(),
                "restart_count": proc.restart_count,
                "last_heartbeat": self.heartbeats.get(name),
                "dependencies": list(self.dep_graph.graph.get(name, [])),
            }
        return snapshot

    def start_process(self, name: str) -> bool:
        proc = self.processes.get(name)
        if proc is None:
            return False
        if proc.process and proc.process.is_alive():
            return True
        deps = self.dep_graph.graph.get(name, [])
        for dep in deps:
            dep_proc = self.processes.get(dep)
            if not dep_proc or not dep_proc.process or not dep_proc.process.is_alive():
                logger.warning("Cannot start %s; dependency %s not running", name, dep)
                return False
        self._spawn(proc)
        return True

    def stop_process(self, name: str) -> bool:
        proc = self.processes.get(name)
        if proc is None:
            return False
        instance = getattr(proc.target, "__self__", None)
        if instance is not None and hasattr(instance, "stop_event"):
            try:
                instance.stop_event.set()
            except Exception:
                pass
        if proc.process and proc.process.is_alive():
            proc.process.join(timeout=10)
        return True

    def restart_process(self, name: str) -> bool:
        proc = self.processes.get(name)
        if proc is None:
            return False
        self._restart(proc)
        return True

    def _spawn(self, proc: ManagedProcess) -> None:
        logger.info("Starting %s", proc.name)
        instance = getattr(proc.target, "__self__", None)
        if instance is not None and hasattr(instance, "stop_event"):
            try:
                instance.stop_event.clear()
            except Exception:
                pass
        proc.process = threading.Thread(target=proc.target, name=proc.name, args=proc.args, daemon=True)
        proc.process.start()
        self.heartbeats[proc.name] = datetime.now(timezone.utc).isoformat()

    def _restart(self, proc: ManagedProcess) -> None:
        if proc.restart_count >= self.MAX_RESTARTS:
            logger.critical("%s exceeded restart limit", proc.name)
            self.shutdown.set()
            return

        proc.restart_count += 1
        self.restart_counts[proc.name] = proc.restart_count
        self._persist_state()
        self.state_manager.set_state(BotLifecycle.RECOVERING)

        instance = getattr(proc.target, "__self__", None)
        if instance is not None and hasattr(instance, "stop_event"):
            try:
                instance.stop_event.set()
            except Exception:
                pass
        if proc.process and proc.process.is_alive():
            proc.process.join(timeout=5)

        self._spawn(proc)
        self.state_manager.set_state(BotLifecycle.RUNNING)

    def _handle_shutdown(self, signum, frame) -> None:
        logger.warning("Supervisor shutdown signal received (%s)", signum)
        self.shutdown.set()
        self.stop_all()

    def start(self) -> None:
        self.state_manager.set_state(BotLifecycle.STARTING)
        order = self.dep_graph.resolve_order()
        logger.info("Startup order: %s", order)
        for name in order:
            self._spawn(self.processes[name])
        self.state_manager.set_state(BotLifecycle.RUNNING)
        self.monitor()

    def stop_all(self) -> None:
        logger.warning("Stopping all processes")
        self.state_manager.set_state(BotLifecycle.STOPPING)
        for proc in self.processes.values():
            instance = getattr(proc.target, "__self__", None)
            if instance is not None and hasattr(instance, "stop_event"):
                try:
                    instance.stop_event.set()
                except Exception:
                    pass
            if proc.process and proc.process.is_alive():
                proc.process.join(timeout=10)
        self.state_manager.set_state(BotLifecycle.STOPPED)

    def monitor(self) -> None:
        logger.info("Supervisor monitor loop started")
        last_status = datetime.now(timezone.utc)
        while not self.shutdown.is_set():
            now = datetime.now(timezone.utc)
            if now - last_status >= self.STATUS_LOG_INTERVAL:
                snapshot = self.get_process_snapshot()
                summary = {
                    name: {
                        "running": meta["running"],
                        "last_heartbeat": meta["last_heartbeat"],
                    }
                    for name, meta in snapshot.items()
                }
                logger.info("Supervisor status: %s", summary)
                last_status = now
            for name, proc in list(self.processes.items()):
                if not proc.process or not proc.process.is_alive():
                    logger.error("Process crashed: %s", name)
                    self._restart(proc)
                    continue

                hb = self.heartbeats.get(name)
                if not hb:
                    continue
                last = datetime.fromisoformat(hb)
                if now - last > self.HEARTBEAT_TIMEOUT:
                    logger.error("Heartbeat timeout: %s", name)
                    self._restart(proc)

            time.sleep(1)
