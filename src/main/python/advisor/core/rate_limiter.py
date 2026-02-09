import time
from collections import deque


class RateLimiter:

    def __init__(self, max_calls, period_sec):
        self.max_calls = max_calls
        self.period = period_sec
        self.calls = deque()

    def allow(self):
        now = time.time()

        while self.calls and now - self.calls[0] > self.period:
            self.calls.popleft()

        if len(self.calls) < self.max_calls:
            self.calls.append(now)
            return True

        return False

    def time_until_next(self):
        if len(self.calls) < self.max_calls:
            return 0

        now = time.time()
        oldest_call = self.calls[0]
        return max(0, self.period - (now - oldest_call))
