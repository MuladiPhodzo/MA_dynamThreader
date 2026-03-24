import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from queue import Queue, Empty, Full
from typing import Any


@dataclass(frozen=True)
class Event:
    type: str
    payload: dict[str, Any] | None
    timestamp: datetime


class EventSubscriber:
    def __init__(self, bus: "EventBus", token: object, queue: Queue):
        self._bus = bus
        self._token = token
        self._queue = queue

    async def next(self, stop_event=None, timeout: float | None = 1.0) -> Event | None:
        """
        Await the next event.
        Returns None if timeout elapses or stop_event is set.
        """
        while True:
            if stop_event is not None and stop_event.is_set():
                return None
            try:
                return await asyncio.to_thread(self._queue.get, True, timeout)
            except Empty:
                if timeout is None:
                    continue
                return None

    def close(self) -> None:
        self._bus.unsubscribe(self._token)


class EventBus:
    def __init__(self, max_queue_size: int = 1000):
        self._lock = threading.Lock()
        self._subs: dict[object, tuple[set[str], Queue]] = {}
        self._max_queue_size = max(10, int(max_queue_size))

    def subscribe(self, *event_types: str) -> EventSubscriber:
        token = object()
        types = set(event_types) if event_types else {"*"}
        queue: Queue = Queue(maxsize=self._max_queue_size)
        with self._lock:
            self._subs[token] = (types, queue)
        return EventSubscriber(self, token, queue)

    def unsubscribe(self, token: object) -> None:
        with self._lock:
            self._subs.pop(token, None)

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        evt = Event(event_type, payload, datetime.now(timezone.utc))
        with self._lock:
            targets = list(self._subs.values())
        for types, queue in targets:
            if "*" in types or event_type in types:
                try:
                    queue.put_nowait(evt)
                except Full:
                    try:
                        queue.get_nowait()
                    except Empty:
                        pass
                    try:
                        queue.put_nowait(evt)
                    except Full:
                        pass

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {",".join(sorted(types)): q.qsize() for types, q in self._subs.values()}
