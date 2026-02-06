class DependencyGraph:

    def __init__(self):
        self.graph = {}

    def add(self, process_name, depends_on=None):
        self.graph[process_name] = depends_on or []

    def resolve_order(self):
        resolved = []
        unresolved = set(self.graph.keys())

        while unresolved:
            progress = False
            for proc in list(unresolved):
                deps = self.graph[proc]
                if all(d in resolved for d in deps):
                    resolved.append(proc)
                    unresolved.remove(proc)
                    progress = True

            if not progress:
                raise RuntimeError("Circular dependency detected")

        return resolved
