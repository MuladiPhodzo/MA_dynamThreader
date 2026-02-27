import logging
import time
from datetime import timedelta, datetime
import sys
from typing import Any, Callable, Optional, List
from .readiness_gate import ReadinessGate

logging.basicConfig(
    level=logging.INFO,
    format="%(astime)s [%(levename)s] %(message)s",
    handlers=[
        logging.FileHandler("MA_DynamAdvisor.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)

logger = logging.getLogger("Process_Scheduler")

class ProcessScheduler:

    def __init__(self, registry):
        self.registry = registry
        self.gate = ReadinessGate(registry)

    def schedule(
        self,
        process_name: str,
        required_resources: List[str],
        task: Callable[[], Any],
        shutdown_event,
        heartbeats: dict,
        timeout: Optional[int] = None,
        poll_interval: float = 0.5,
    ) -> Optional[Any]:
        """
        Blocks execution until all required resources are ready,
        then executes the task safely.
        """

        start = datetime.now(datetime.timezone.utc)

        # ---------------------------
        # WAIT FOR RESOURCES
        # ---------------------------
        while not shutdown_event.is_set():

            # heartbeat while waiting
            heartbeats[process_name] = datetime.now(datetime.timezone.utc).isoformat()

            if self._resources_ready(required_resources):
                break

            if timeout and datetime.now(datetime.timezone.utc) - start > timedelta(seconds=timeout):
                logger.error(
                    f"[{process_name}] Scheduler timeout waiting for {required_resources}"
                )
                return None

            time.sleep(poll_interval)

        if shutdown_event.is_set():
            logger.info(f"[{process_name}] Shutdown before execution")
            return None

        # ---------------------------
        # EXECUTE TASK
        # ---------------------------
        try:
            logger.info(
                f"[{process_name}] Running task (resources ready: {required_resources})"
            )
            return task()

        except Exception:
            logger.exception(f"[{process_name}] Task execution failed")
            raise
