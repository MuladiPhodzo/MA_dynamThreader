import time
from multiprocessing import Manager


class HeartbeatRegistry:
    def __init__(self):
        self.manager = Manager()
        self.beats = self.manager.dict()

    def beat(self, name: str):
        """Update heartbeat timestamp"""
        self.beats[name] = time.time()

    def last_seen(self, name: str):
        return self.beats.get(name)

    def remove(self, name: str):
        if name in self.beats:
            del self.beats[name]
