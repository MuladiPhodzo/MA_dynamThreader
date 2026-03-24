import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Awaitable, Callable, Optional, Union

from .requirements import ProcessRequirement
from .readiness_gate import ReadinessGate
from advisor.utils.logging_setup import get_logger

logger = get_logger("Process_Scheduler")


class ProcessScheduler:
    def __init__(
        self,
        registry=None,
        max_concurrent_tasks: int = 5,
        default_timeout: int = 60,
    ):
        self.registry = registry
        self.gate = ReadinessGate(registry)
        self._semaphore = asyncio.Semaphore(max_concurrent_tasks)
        self.default_timeout = default_timeout

    async def schedule(
        self,
        process_name: str,
        required_resources: list[Union[ProcessRequirement, str]],
        task: Callable[[], Any] | Callable[[], Awaitable[Any]],
        shutdown_event,
        heartbeats: dict,
        timeout: Optional[int] = None,
    ) -> Optional[Any]:
        """
        Schedules a task with:
        - resource gating
        - timeout enforcement
        - graceful shutdown
        - concurrency control
        """

        timeout = timeout or self.default_timeout
        start_time = datetime.now(timezone.utc)

        requirements = self._normalize_requirements(required_resources)

        try:
            # -------------------------
            # 1. Wait for dependencies
            # -------------------------
            if requirements:
                await self._wait_for_requirements(
                    process_name,
                    requirements,
                    shutdown_event,
                    timeout,
                )

            # -------------------------
            # 2. Shutdown check
            # -------------------------
            if shutdown_event.is_set():
                logger.info("[%s] Skipped (shutdown requested)", process_name)
                return None

            # -------------------------
            # 3. Execute with concurrency control
            # -------------------------
            async with self._semaphore:
                result = await self._execute_task(task, timeout, process_name)

            # -------------------------
            # 4. Success bookkeeping
            # -------------------------
            heartbeats[process_name] = datetime.now(timezone.utc).isoformat()

            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            logger.info("[%s] Completed in %.2fs", process_name, duration)

            return result

        except asyncio.TimeoutError:
            logger.error("[%s] Task timed out (%ss)", process_name, timeout)
            return None

        except InterruptedError:
            logger.warning("[%s] Interrupted due to shutdown", process_name)
            return None

        except Exception as e:
            logger.exception("[%s] Execution failed: %s", process_name, e)
            raise

    # ---------------------------------------
    # Internal Helpers
    # ---------------------------------------

    async def _wait_for_requirements(
        self,
        process_name: str,
        requirements: list[ProcessRequirement],
        shutdown_event,
        timeout: int,
    ):
        """
        Waits for resources with shutdown awareness.
        """

        def blocking_wait():
            start = datetime.now(timezone.utc)

            while True:
                if shutdown_event.is_set():
                    raise InterruptedError("Shutdown during resource wait")

                if self.gate.is_ready(requirements):
                    return

                elapsed = (datetime.now(timezone.utc) - start).total_seconds()
                if elapsed > timeout:
                    raise TimeoutError("Resource wait timeout")

                # small sleep to avoid busy loop
                import time
                time.sleep(0.5)

        await asyncio.to_thread(blocking_wait)

    async def _execute_task(
        self,
        task: Callable[[], Any] | Callable[[], Awaitable[Any]],
        timeout: int,
        process_name: str,
    ) -> Any:
        """
        Executes both sync and async tasks safely with timeout.
        """

        if asyncio.iscoroutinefunction(task):
            return await asyncio.wait_for(task(), timeout=timeout)

        # Run sync task safely in thread
        return await asyncio.wait_for(
            asyncio.to_thread(task),
            timeout=timeout,
        )

    @staticmethod
    def _normalize_requirements(
        required_resources: list[Union[ProcessRequirement, str]],
    ) -> list[ProcessRequirement]:
        """
        Ensures all requirements are ProcessRequirement objects.
        Adds safe defaults where needed.
        """

        normalized: list[ProcessRequirement] = []

        for item in required_resources:
            if isinstance(item, ProcessRequirement):
                normalized.append(item)
            elif isinstance(item, str):
                normalized.append(
                    ProcessRequirement(
                        resource=item,
                        max_age=timedelta(seconds=60),  # default safeguard
                    )
                )
            else:
                raise TypeError(
                    f"Invalid requirement type: {type(item)}"
                )

        return normalized
