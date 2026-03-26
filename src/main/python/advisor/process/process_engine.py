import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from multiprocessing.managers import SyncManager
from pathlib import Path
from threading import Event, Thread, Timer
import threading

from advisor.core import dependency_graph, health_bus
from advisor.core.state import BotLifecycle, StateManager
from advisor.process.heartbeats import HeartbeatRegistry
from advisor.scheduler.resource_registry import ResourceRegistry
from advisor.utils.logging_setup import get_logger

logger = get_logger("Orchestrator")


@dataclass
class ManagedProcess:
    name: str
    target: callable
    args: tuple = field(default_factory=tuple)
    dependencies: list[str] = field(default_factory=list)
    process: Thread | None = None
    restart_count: int = 0
    stop_event: Event = field(default_factory=Event)
    heartbeat_timer: Timer | None = None


class Supervisor:
    STATE_FILE = Path("runtime/supervisor_state.json")
    MAX_RESTARTS = 5
    HEARTBEAT_TIMEOUT = timedelta(minutes=1)
    STATUS_LOG_INTERVAL = timedelta(seconds=30)

    def __init__(self, shutdown_event: Event, manager: SyncManager, state_manager: StateManager, heartbeats: HeartbeatRegistry):
        self.shutdown = shutdown_event
        self.state_manager = state_manager
        # self.registry = ResourceRegistry(self.manager)
        self.health_bus = health_bus.HealthBus(self.state_manager._manager)
        self.heartbeats = heartbeats.beats
        self.dep_graph = dependency_graph.DependencyGraph()
        self.processes: dict[str, ManagedProcess] = {}
        self.restart_counts: dict[str, int] = {}
        self.last_backtest = state_manager.last_backtest_run

        self._load_state()
        # Register OS signals
        import signal
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    # -------------------------------
    # STATE PERSISTENCE
    # -------------------------------
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

    # -------------------------------
    # PROCESS MANAGEMENT
    # -------------------------------
    def register_process(
        self,
        name: str,
        target,
        *args,
        depends: list[str] | None = None,
        event_driven: bool = False,
    ):
        """
        Register a process with the supervisor.

        - name: process name
        - target: callable or event-driven process instance
        - args: arguments to pass if target is callable
        - depends: list of process names this process depends on
        - event_driven: if True, target is an event-driven process and does not need .start()
        """
        proc = ManagedProcess(
            name=name,
            target=target,
            args=args,
            dependencies=depends or [],
        )
        if event_driven:
            # For event-driven processes, we assume they manage their own lifecycle and heartbeats
            logger.info("Registering event-driven process: %s", name)
            target.register()
        # Mark as event-driven internally
        setattr(proc, "_event_driven", event_driven)

        self.processes[name] = proc
        self.restart_counts.setdefault(name, 0)
        self.dep_graph.add(name, depends or [])

        logger.info(
            "Registered process: %s (depends on: %s, event_driven=%s)",
            name,
            depends or [],
            event_driven,
        )

    def _spawn(self, proc: ManagedProcess) -> None:
        if getattr(proc, "_event_driven", False):
            logger.info("Skipping spawn for event-driven process: %s", proc.name)
            return

        logger.info("Starting %s", proc.name)
        instance = getattr(proc.target, "__self__", None)
        if instance and hasattr(instance, "stop_event"):
            try:
                instance.stop_event.clear()
            except Exception:
                pass

        proc.process = threading.Thread(
            target=proc.target, name=proc.name, args=proc.args, daemon=True
        )
        proc.process.start()
        self.heartbeats[proc.name] = datetime.now(timezone.utc).isoformat()

    def _process_wrapper(self, proc: ManagedProcess):
        try:
            proc.target(*proc.args)
        except Exception:
            logger.exception("Process %s crashed.", proc.name)
            self._restart(proc)

    def _restart(self, proc: ManagedProcess):
        if proc.restart_count >= self.MAX_RESTARTS:
            logger.critical("Process %s exceeded restart limit", proc.name)
            self.shutdown.set()
            return
        proc.restart_count += 1
        self.restart_counts[proc.name] = proc.restart_count
        self._persist_state()
        logger.warning("Restarting process %s (%d/%d)", proc.name, proc.restart_count, self.MAX_RESTARTS)

        # Signal stop
        proc.stop_event.set()
        if proc.process and proc.process.is_alive():
            proc.process.join(timeout=5)
        self._spawn(proc)

    def _schedule_heartbeat_check(self, proc: ManagedProcess):
        if self.shutdown.is_set():
            return

        def check():
            if self.shutdown.is_set():
                return
            last_hb = self.heartbeats.get(proc.name)
            now = datetime.now(timezone.utc)
            if not last_hb:
                # No heartbeat yet, reschedule
                self._schedule_heartbeat_check(proc)
                return
            last = datetime.fromisoformat(last_hb)
            if now - last > self.HEARTBEAT_TIMEOUT:
                logger.error("Heartbeat timeout: %s", proc.name)
                self._restart(proc)
            # Reschedule next heartbeat check
            self._schedule_heartbeat_check(proc)

        proc.heartbeat_timer = Timer(self.HEARTBEAT_TIMEOUT.total_seconds(), check)
        proc.heartbeat_timer.daemon = True
        proc.heartbeat_timer.start()

    # -------------------------------
    # START / STOP
    # -------------------------------
    def start(self):
        self.state_manager.set_state(BotLifecycle.STARTING)
        order = self.dep_graph.resolve_order()
        logger.info("Startup order: %s", order)
        for name in order:
            self._spawn(self.processes[name])
        self.state_manager.set_state(BotLifecycle.RUNNING)

    def stop_all(self):
        logger.warning("Stopping all processes")
        self.state_manager.set_state(BotLifecycle.STOPPING)
        for proc in self.processes.values():
            proc.stop_event.set()
            if proc.process and proc.process.is_alive():
                proc.process.join(timeout=10)
            if proc.heartbeat_timer:
                proc.heartbeat_timer.cancel()
        self._persist_state()
        self.state_manager.set_state(BotLifecycle.STOPPED)

    def _handle_shutdown(self, signum, frame):
        logger.warning("Supervisor shutdown signal received: %s", signum)
        self.shutdown.set()
        self.stop_all()