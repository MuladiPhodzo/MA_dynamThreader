from enum import Enum
from dataclasses import dataclass
from datetime import datetime, timedelta


class ResourceState(Enum):
    MISSING = "MISSING"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    STALE = "STALE"
    CORRUPT = "CORRUPT"


@dataclass
class ResourceStatus:
    state: ResourceState
    last_updated: datetime | None = None

    def is_fresh(self, max_age: timedelta):
        if not self.last_updated:
            return False
        return datetime.utcnow() - self.last_updated <= max_age
