import asyncio
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from .requirements import ProcessRequirement
from .readiness_gate import ReadinessGate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("MA_DynamAdvisor.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("Process_Scheduler")


class ProcessScheduler:
    def __init__(self, registry=None):
        self.registry = registry
        self.gate = ReadinessGate(registry)

    async def schedule(
        self,
        process_name: str,
        required_resources: list[ProcessRequirement | str],
        task: Callable[[], Any] | Callable[[], Awaitable[Any]],
        shutdown_event,
        heartbeats: dict,
        timeout: Optional[int] = None,
    ) -> Optional[Any]:
        heartbeats[process_name] = datetime.now(timezone.utc).isoformat()

        requirements = self._normalize_requirements(required_resources)
        if requirements:
            try:
                await asyncio.to_thread(self.gate.wait_for, requirements, timeout or 60)
            except TimeoutError:
                logger.error(
                    "[%s] Scheduler timeout waiting for %s",
                    process_name,
                    [r.resource for r in requirements],
                )
                return None

        if shutdown_event.is_set():
            logger.info("[%s] Shutdown before execution", process_name)
            return None

        try:
            result = task()
            if asyncio.iscoroutine(result):
                return await result
            return result
        except Exception:
            logger.exception("[%s] Task execution failed", process_name)
            raise

    @staticmethod
    def _normalize_requirements(
        required_resources: list[ProcessRequirement | str],
    ) -> list[ProcessRequirement]:
        normalized: list[ProcessRequirement] = []
        for item in required_resources:
            if isinstance(item, ProcessRequirement):
                normalized.append(item)
            else:
                normalized.append(ProcessRequirement(resource=item))
        return normalized
