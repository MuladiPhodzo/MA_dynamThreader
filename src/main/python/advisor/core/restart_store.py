import json
import os

class RestartStore:
    FILE = "runtime/restarts.json"

    def __init__(self):
        os.makedirs("runtime", exist_ok=True)
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.FILE):
            with open(self.FILE, "r") as f:
                return json.load(f)
        return {}

    def increment(self, name):
        self.data[name] = self.data.get(name, 0) + 1
        self._save()

    def get(self, name):
        return self.data.get(name, 0)

    def _save(self):
        with open(self.FILE, "w") as f:
            json.dump(self.data, f, indent=2)

    def reset(self, name):
        if name in self.data:
            del self.data[name]
            self._save()
