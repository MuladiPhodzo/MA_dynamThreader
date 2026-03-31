import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from advisor.utils.logging_setup import get_logger

logger = get_logger("EventBus")


# =========================================================
# Event Model
# =========================================================

@dataclass(frozen=True)
class Event:
    type: str
    payload: dict[str, Any] | None
    timestamp: datetime


# =========================================================
# Topic Helper (CRITICAL for partitioning)
# =========================================================

def topic(event: str, symbol: str | None = None) -> str:
    return f"{event}:{symbol}" if symbol else event


# =========================================================
# Event Subscriber
# =========================================================

class EventSubscriber:
    def __init__(self, bus: "EventBus", token: object, queue: asyncio.Queue):
        self._bus = bus
        self._token = token
        self._queue = queue

    async def next(
        self,
        stop_event: asyncio.Event | None = None,
        timeout: float | None = None,
    ) -> Event | None:
        """
        Await next event.
        Returns None on timeout or shutdown.
        """
        try:
            if stop_event:
                done, _ = await asyncio.wait(
                    [
                        asyncio.create_task(self._queue.get()),
                        asyncio.create_task(stop_event.wait()),
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                task = list(done)[0]

                if task.cancelled():
                    return None

                result = task.result()

                if isinstance(result, Event):
                    return result
                return None

            if timeout:
                return await asyncio.wait_for(self._queue.get(), timeout)

            return await self._queue.get()

        except asyncio.TimeoutError:
            return None

    def close(self) -> None:
        self._bus.unsubscribe(self._token)


# =========================================================
# Event Bus
# =========================================================

class EventBus:
    """
    Async-first, thread-safe event bus with:
    - topic partitioning (EVENT:SYMBOL)
    - wildcard routing (EVENT:*, *)
    - backpressure handling
    """

    def __init__(self, max_queue_size: int = 1000):
        self._lock = threading.Lock()
        self._subs: dict[
            object, tuple[set[str], asyncio.Queue, asyncio.AbstractEventLoop | None]
        ] = {}
        self._max_queue_size = max(10, int(max_queue_size))
        self._loop: asyncio.AbstractEventLoop | None = None
        self._handler_tasks: dict[object, asyncio.Task] = {}
        self._stats_lock = threading.Lock()
        self._dropped = 0
        self._dropped_by_reason: dict[str, int] = {}

    def _bind_loop(self) -> asyncio.AbstractEventLoop | None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return self._loop
        self._loop = loop
        return loop

    # -----------------------------------------------------
    # Subscription
    # -----------------------------------------------------

    def subscribe(
        self,
        *event_types: str,
        handler: Callable[[Event], Awaitable[None] | None] | None = None,
    ) -> EventSubscriber:
        """
        Subscribe to specific topics or patterns.

        Examples:
            "MARKET_DATA_READY:EURUSD"
            "MARKET_DATA_READY:*"
            "*"
        """
        raw_types = list(event_types)
        if handler is None:
            for item in reversed(raw_types):
                if callable(item):
                    handler = item
                    break
            if handler is not None:
                raw_types = [item for item in raw_types if not callable(item)]

        loop = self._bind_loop()
        token = object()
        types = {t for t in raw_types if isinstance(t, str)} if raw_types else set()
        if not types:
            types = {"*"}
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue_size)

        with self._lock:
            self._subs[token] = (types, queue, loop)

        subscriber = EventSubscriber(self, token, queue)
        if handler is not None:
            if loop is None or not loop.is_running():
                raise RuntimeError("EventBus.subscribe with handler requires a running asyncio loop")
            self._start_handler(token, subscriber, handler, loop)

        return subscriber

    def unsubscribe(self, token: object) -> None:
        with self._lock:
            self._subs.pop(token, None)
        task = self._handler_tasks.pop(token, None)
        if task:
            task.cancel()

    def _start_handler(
        self,
        token: object,
        subscriber: EventSubscriber,
        handler: Callable[[Event], Awaitable[None] | None],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        async def _consume() -> None:
            try:
                while True:
                    event = await subscriber.next(timeout=1.0)
                    if event is None:
                        continue
                    try:
                        result = handler(event)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        logger.exception("Event handler failed: %s", getattr(event, "type", "unknown"))
            except asyncio.CancelledError:
                return

        self._handler_tasks[token] = loop.create_task(_consume())

    # -----------------------------------------------------
    # Matching Logic (Wildcard Support)
    # -----------------------------------------------------

    @staticmethod
    def _matches(subscribed: set[str], event_type: str) -> bool:
        for pattern in subscribed:
            if pattern == "*":
                return True
            if pattern.endswith("*") and event_type.startswith(pattern[:-1]):
                return True
            if pattern == event_type:
                return True
        return False

    # -----------------------------------------------------
    # Async Publish (Preferred)
    # -----------------------------------------------------

    async def publish(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        current_loop = self._bind_loop()
        evt = Event(event_type, payload, datetime.now(timezone.utc))

        with self._lock:
            targets = list(self._subs.values())

        for types, queue, loop in targets:
            if self._matches(types, event_type):
                if loop is None or loop is current_loop:
                    await self._safe_put_async(queue, evt)
                elif loop.is_running():
                    loop.call_soon_threadsafe(self._safe_put_nowait, queue, evt)
                else:
                    self._record_drop("no_loop")

    # -----------------------------------------------------
    # Sync Emit (Thread-safe)
    # -----------------------------------------------------

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """
        Thread-safe emit (for external systems like MT5 callbacks).
        """
        evt = Event(event_type, payload, datetime.now(timezone.utc))

        with self._lock:
            targets = list(self._subs.values())

        for types, queue, loop in targets:
            if not self._matches(types, event_type):
                continue
            target_loop = loop or self._loop
            if target_loop is None or not target_loop.is_running():
                logger.warning("EventBus.emit dropped event (no running loop): %s", event_type)
                self._record_drop("no_loop")
                continue
            target_loop.call_soon_threadsafe(self._safe_put_nowait, queue, evt)

    # -----------------------------------------------------
    # Backpressure Handling
    # -----------------------------------------------------

    async def _safe_put_async(self, queue: asyncio.Queue, evt: Event):
        if queue.full():
            try:
                queue.get_nowait()  # drop oldest
                self._record_drop("queue_full")
            except asyncio.QueueEmpty:
                pass

        try:
            await queue.put(evt)
        except asyncio.QueueFull:
            self._record_drop("queue_full")
            pass

    def _safe_put_nowait(self, queue: asyncio.Queue, evt: Event):
        if queue.full():
            try:
                queue.get_nowait()
                self._record_drop("queue_full")
            except asyncio.QueueEmpty:
                pass

        try:
            queue.put_nowait(evt)
        except asyncio.QueueFull:
            self._record_drop("queue_full")
            pass

    def _record_drop(self, reason: str) -> None:
        with self._stats_lock:
            self._dropped += 1
            self._dropped_by_reason[reason] = self._dropped_by_reason.get(reason, 0) + 1

    def drop_metrics(self) -> dict[str, int]:
        with self._stats_lock:
            metrics = {"dropped_total": self._dropped}
            for reason, count in self._dropped_by_reason.items():
                metrics[f"dropped_{reason}"] = count
            return metrics

    # -----------------------------------------------------
    # Monitoring
    # -----------------------------------------------------

    def snapshot(self) -> dict[str, int]:
        """
        Returns queue sizes per subscription pattern.
        Useful for debugging / monitoring.
        """
        with self._lock:
            return {
                ",".join(sorted(types)): queue.qsize()
                for types, queue, _loop in self._subs.values()
            }


# =========================================================
# Consumer Helper (HIGH VALUE)
# =========================================================

async def consume_forever(
    subscriber: EventSubscriber,
    handler: Callable[[Event], Awaitable[None]],
    stop_event: asyncio.Event,
    timeout: float = 1.0,
):
    """
    Utility loop for event-driven processes.
    """
    while not stop_event.is_set():
        event = await subscriber.next(timeout=timeout)
        if event is None:
            continue

        await handler(event)
