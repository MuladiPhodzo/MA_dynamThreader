import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable


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
        self._subs: dict[object, tuple[set[str], asyncio.Queue]] = {}
        self._max_queue_size = max(10, int(max_queue_size))

    # -----------------------------------------------------
    # Subscription
    # -----------------------------------------------------

    def subscribe(self, *event_types: str) -> EventSubscriber:
        """
        Subscribe to specific topics or patterns.

        Examples:
            "MARKET_DATA_READY:EURUSD"
            "MARKET_DATA_READY:*"
            "*"
        """
        token = object()
        types = set(event_types) if event_types else {"*"}
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue_size)

        with self._lock:
            self._subs[token] = (types, queue)

        return EventSubscriber(self, token, queue)

    def unsubscribe(self, token: object) -> None:
        with self._lock:
            self._subs.pop(token, None)

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
        evt = Event(event_type, payload, datetime.now(timezone.utc))

        with self._lock:
            targets = list(self._subs.values())

        for types, queue in targets:
            if self._matches(types, event_type):
                await self._safe_put_async(queue, evt)

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

        loop = asyncio.get_event_loop()

        for types, queue in targets:
            if self._matches(types, event_type):
                loop.call_soon_threadsafe(self._safe_put_nowait, queue, evt)

    # -----------------------------------------------------
    # Backpressure Handling
    # -----------------------------------------------------

    async def _safe_put_async(self, queue: asyncio.Queue, evt: Event):
        if queue.full():
            try:
                queue.get_nowait()  # drop oldest
            except asyncio.QueueEmpty:
                pass

        try:
            await queue.put(evt)
        except asyncio.QueueFull:
            pass

    def _safe_put_nowait(self, queue: asyncio.Queue, evt: Event):
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass

        try:
            queue.put_nowait(evt)
        except asyncio.QueueFull:
            pass

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
                for types, queue in self._subs.values()
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