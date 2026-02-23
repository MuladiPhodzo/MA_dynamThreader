from datetime import datetime
from multiprocessing.managers import SyncManager
from .resources import ResourceStatus, ResourceState


class ResourceRegistry:

    def __init__(self, manager: SyncManager):
        self._resources = manager.dict()

    def register(self, name: str):
        self._resources[name] = ResourceStatus(ResourceState.INITIALIZING)

    def set_ready(self, name: str):
        self._resources[name] = ResourceStatus(
            ResourceState.READY,
            datetime.now(datetime.timezone.utc)
        )

    def set_state(self, name: str, state: ResourceState):
        self._resources[name] = ResourceStatus(
            state,
            datetime.now(datetime.timezone.utc)
        )

    def get(self, name: str) -> ResourceStatus | None:
        return self._resources.get(name)

    def snapshot(self):
        return dict(self._resources)
