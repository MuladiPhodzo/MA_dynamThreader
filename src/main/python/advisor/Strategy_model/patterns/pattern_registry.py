from __future__ import annotations

import pandas as pd

from advisor.utils.logging_setup import get_logger

logger = get_logger("PatternRegistry")


class PatternRegistry:
    _REGISTRY: dict[str, type] = {}
    _REGISTRY_MULTI: dict[str, list[dict[str, object]]] = {}

    @classmethod
    def register(cls, name: str, type: str):
        def decorator(pattern_cls):
            key = name.lower()
            entry = {"type": type.lower(), "cls": pattern_cls}
            cls._REGISTRY[key] = entry
            cls._REGISTRY_MULTI.setdefault(key, []).append(entry)
            return pattern_cls

        return decorator

    def __init__(self, config: dict | None = None):
        self.patterns = config.get("patterns", {})
        self.params = config.get("params", {})
        self.instances: dict[str, object] = {}

    def build(self):
        for name in self.patterns:
            entries = self._REGISTRY_MULTI.get(name.lower())
            if not entries:
                raise ValueError(f"Pattern '{name}' not registered")
            for entry in entries:
                cls = entry["cls"]
                pattern_type = entry["type"]
                try:
                    instance = cls(**self.params)
                except TypeError:
                    instance = cls.__new__(cls)
                instance.params = dict(self.params or {})
                instance.name = name
                instance._type = pattern_type
                instance._name = name
                instance_key = f"{name}:{pattern_type}:{cls.__name__}"
                self.instances[instance_key] = instance
        return self.instances

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        features = df.copy()
        for name, pattern in self.instances.items():
            try:
                result = pattern.compute(features)
                if isinstance(result, pd.DataFrame):
                    features = result
                else:
                    features[name] = result
            except Exception as exc:
                logger.warning("pattern %s failed: %s", name, exc)
        return features


# Import built-in detectors for registration side effects.
from advisor.Strategy_model.patterns import double_patterns  # noqa: F401,E402
from advisor.Strategy_model.patterns import quasimodo  # noqa: F401,E402
from advisor.Strategy_model.patterns import shoulder_patterns  # noqa: F401,E402
