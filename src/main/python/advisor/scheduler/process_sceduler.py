import asyncio
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
import inspect
from threading import Lock
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
        per_process_limits: Optional[dict[str, int]] = None,
        single_flight: bool = True,
    ):
        self.registry = registry
        self.gate = ReadinessGate(registry)
        self.default_timeout = default_timeout
        self._process_limits = per_process_limits or {}
        self._single_flight = single_flight
        self._max_concurrent_tasks = max_concurrent_tasks
        self._loop_state: dict[int, "_LoopState"] = {}
        self._loop_state_lock = Lock()

    def _get_loop_state(self) -> "_LoopState":
        loop = asyncio.get_running_loop()
        key = id(loop)
        with self._loop_state_lock:
            state = self._loop_state.get(key)
            if state is None:
                state = _LoopState(
                    loop=loop,
                    global_semaphore=asyncio.Semaphore(self._max_concurrent_tasks),
                )
                self._loop_state[key] = state
            return state

    def _set_current(self, process_name: str) -> None:
        state = self._get_loop_state()
        state.current_process = process_name
        logger.info("[%s] Running", process_name)

    def _clear_current(self, process_name: str) -> None:
        state = self._get_loop_state()
        if state.current_process == process_name:
            state.current_process = None

    def _get_process_semaphore(self, process_name: str) -> Optional[asyncio.Semaphore]:
        if not self._single_flight and process_name not in self._process_limits:
            return None
        state = self._get_loop_state()
        sem = state.process_semaphores.get(process_name)
        if sem is not None:
            return sem
        limit = self._process_limits.get(process_name)
        if limit is None:
            limit = 1 if self._single_flight else None
        if limit is None:
            return None
        limit = max(1, int(limit))
        sem = asyncio.Semaphore(limit)
        state.process_semaphores[process_name] = sem
        return sem

    async def schedule(
        self,
        process_name: str,
        required_resources: list[Union[ProcessRequirement, str]],
        task: Callable[[], Any] | Callable[[], Awaitable[Any]],
        shutdown_event,
        heartbeats: dict,
        timeout: Optional[float] = None,
    ) -> Optional[Any]:
        """
        Schedules a task with:
        - resource gating
        - timeout enforcement
        - graceful shutdown
        - concurrency control
        """

        timeout = float(timeout or self.default_timeout)
        start_time = datetime.now(timezone.utc)
        requirements = self._normalize_requirements(required_resources)
        ran = False

        try:
            await self._prepare_and_execute(
                process_name,
                requirements,
                task,
                shutdown_event,
                heartbeats,
                timeout,
                start_time,
            )
            ran = True
            return await self._handle_success(
                process_name, heartbeats, start_time
            )

        except asyncio.TimeoutError:
            self._handle_timeout_error(process_name, timeout, ran)
            return None

        except TimeoutError:
            self._handle_resource_timeout(process_name, timeout, ran)
            return None

        except InterruptedError:
            self._handle_interrupted(process_name, ran)
            return None

        except Exception as e:
            self._handle_general_error(process_name, e, ran)
            return None

        finally:
            if ran:
                self._clear_current(process_name)

    async def _prepare_and_execute(
        self,
        process_name: str,
        requirements: list[ProcessRequirement],
        task: Callable[[], Any] | Callable[[], Awaitable[Any]],
        shutdown_event,
        heartbeats: dict,
        timeout: float,
        start_time: datetime,
    ) -> None:
        """Prepare resources and execute task."""
        if requirements:
            await self._wait_for_requirements(
                process_name,
                requirements,
                shutdown_event,
                timeout,
            )

        if shutdown_event.is_set():
            logger.debug("[%s] Skipped (shutdown requested)", process_name)
            raise InterruptedError("Shutdown requested")

        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        remaining = timeout - elapsed
        if remaining <= 0:
            logger.error("[%s] Task timed out (%ss)", process_name, timeout)
            raise asyncio.TimeoutError()

        await self._run_with_concurrency_control(
            process_name, task, remaining
        )

    async def _handle_success(
        self,
        process_name: str,
        heartbeats: dict,
        start_time: datetime,
    ) -> Any:
        """Handle successful execution."""
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        if duration > 10:
            logger.info("[%s] Completed in %.2fs", process_name, duration)
        else:
            logger.debug("[%s] Completed in %.2fs", process_name, duration)
        heartbeats[process_name] = datetime.now(timezone.utc).isoformat()
        logger.info("[%s] Completed in %.2fs", process_name, duration)
        return None

    def _handle_timeout_error(
        self, process_name: str, timeout: float, ran: bool
    ) -> None:
        """Handle asyncio timeout error."""
        if ran:
            logger.error("[%s] Task timed out (%ss)", process_name, timeout)
        else:
            logger.debug("[%s] Task timed out before run (%ss)", process_name, timeout)

    def _handle_resource_timeout(
        self, process_name: str, timeout: float, ran: bool
    ) -> None:
        """Handle resource wait timeout."""
        if ran:
            logger.error("[%s] Resource wait timed out (%ss)", process_name, timeout)
        else:
            logger.debug("[%s] Resource wait timed out (%ss)", process_name, timeout)

    def _handle_interrupted(self, process_name: str, ran: bool) -> None:
        """Handle interruption."""
        if ran:
            logger.warning("[%s] Interrupted due to shutdown", process_name)
        else:
            logger.debug("[%s] Interrupted due to shutdown", process_name)

    def _handle_general_error(
        self, process_name: str, error: Exception, ran: bool
    ) -> None:
        """Handle general execution errors."""
        if ran:
            logger.exception("[%s] Execution failed: %s", process_name, error)
        else:
            logger.debug("[%s] Execution failed before run: %s", process_name, error)

    async def _run_with_concurrency_control(
        self,
        process_name: str,
        task: Callable[[], Any] | Callable[[], Awaitable[Any]],
        timeout: float,
    ) -> Any:
        """
        Executes task with concurrency control (per-process and global semaphores).
        """
        proc_sem = self._get_process_semaphore(process_name)
        if proc_sem is None:
            async with self._get_loop_state().global_semaphore:
                self._set_current(process_name)
                return await self._execute_task(task, timeout, process_name)
        else:
            # Acquire per-process and global concurrency in a consistent order.
            async with proc_sem:
                async with self._get_loop_state().global_semaphore:
                    self._set_current(process_name)
                    return await self._execute_task(task, timeout, process_name)

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
        if self.gate.registry is None:
            logger.warning("[%s] Resource registry unavailable; skipping readiness gating.", process_name)
            return

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
        timeout: float,
        process_name: str,
    ) -> Any:
        """
        Executes both sync and async tasks safely with timeout.
        """

        if inspect.iscoroutinefunction(task):
            return await asyncio.wait_for(task(), timeout=timeout)

        # Run sync task safely in thread
        result = await asyncio.wait_for(
            asyncio.to_thread(task),
            timeout=timeout,
        )
        if asyncio.iscoroutine(result):
            return await asyncio.wait_for(result, timeout=timeout)
        return result

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


@dataclass
class _LoopState:
    loop: asyncio.AbstractEventLoop
    global_semaphore: asyncio.Semaphore
    process_semaphores: dict[str, asyncio.Semaphore] = field(default_factory=dict)
    current_process: str | None = None
