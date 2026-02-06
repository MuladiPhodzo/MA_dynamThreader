import yaml
import os
import json
from pathlib import Path
from typing import Optional, Dict


class ConfigLoader:
    def __init__(self, config_dir: Path):
        self.config_dir = config_dir

    def yaml_load(self, name: str) -> Optional[Dict]:
        path = self.config_dir / f"{name}.yaml"
        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception:
            return None

        path = self.config_dir / f"{name}.json"
        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data[name]
        except Exception:
            return None

    def yaml_save(self, name: str, data: Dict):
        path = self.config_dir / f"{name}.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f)

    def json_save(self, name: str, data: Dict):
        path = self.config_dir / f"{name}.json"
        if os.path.exists():
            with open(path, "a", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
