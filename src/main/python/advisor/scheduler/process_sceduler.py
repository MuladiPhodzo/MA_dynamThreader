from .readiness_gate import ReadinessGate


class ProcessScheduler:

    def __init__(self, registry):
        self.registry = registry
        self.gate = ReadinessGate(registry)

    def schedule(self, process_name, requirements, target, *args):
        print(f"[Scheduler] Waiting for resources for {process_name}")

        self.gate.wait_for(requirements)

        print(f"[Scheduler] Starting {process_name}")
        return target(*args)
