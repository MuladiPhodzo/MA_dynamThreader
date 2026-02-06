import json
import os
import time
from threading import Thread


class ConfigWatcher(Thread):

    def __init__(self, path, callback):
        super().__init__(daemon=True)
        self.path = path
        self.callback = callback
        self.last_modified = None
        self.running = True

    def run(self):
        while self.running:
            if os.path.exists(self.path):
                modified = os.path.getmtime(self.path)

                if self.last_modified is None:
                    self.last_modified = modified

                elif modified != self.last_modified:
                    self.last_modified = modified
                    with open(self.path) as f:
                        cfg = json.load(f)
                        self.callback(cfg)

            time.sleep(5)

    def stop(self):
        self.running = False
