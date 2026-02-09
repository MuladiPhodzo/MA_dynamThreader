from multiprocessing.managers import SyncManager
from datetime import datetime


class HealthBus:

    def __init__(self, manager: SyncManager):
        self.data = manager.dict()

    def update(self, proc_name, status, meta=None):
        self.data[proc_name] = {
            "status": status,
            "meta": meta or {},
            "timestamp": datetime.utcnow().isoformat()
        }

    def snapshot(self):
        return dict(self.data)
