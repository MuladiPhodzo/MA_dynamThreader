import time
from .resources import ResourceState
from .resource_registry import ResourceRegistry
from .requirements import ProcessRequirement


class ReadinessGate:

    def __init__(self, registry: ResourceRegistry):
        self.registry = registry

    def wait_for(self, requirements: list[ProcessRequirement], timeout=60):
        start = time.time()

        while True:
            unmet = []

            for req in requirements:
                status = self.registry.get(req.resource)

                if status is None:
                    unmet.append(req.resource)
                    continue

                if status.state != ResourceState.READY:
                    unmet.append(req.resource)
                    continue

                if req.max_age and not status.is_fresh(req.max_age):
                    unmet.append(req.resource)

            if not unmet:
                return True

            if time.time() - start > timeout:
                raise TimeoutError(
                    f"Resources not ready: {unmet}"
                )

            time.sleep(1)
