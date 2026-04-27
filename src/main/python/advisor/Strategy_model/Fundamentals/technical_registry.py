class TechnicalRegistry:
    """
    Dynamic indicator loader + executor
    """

    _REGISTRY = {}

    @staticmethod
    def _normalize_name(name: str) -> str:
        text = str(name or "").strip().lower()
        return "".join(ch for ch in text if ch.isalnum())

    @classmethod
    def register(cls, name: str, type: str | None = None):
        def decorator(technical_cls):
            entry = {
                "cls": technical_cls,
                "type": (type or getattr(technical_cls, "_type", "structure")).lower(),
            }
            raw = str(name or "").strip().lower()
            aliases = {
                raw,
                raw.replace("_", " "),
                raw.replace(" ", "_"),
            }
            for alias in aliases:
                key = cls._normalize_name(alias)
                if key:
                    cls._REGISTRY[key] = entry
            return technical_cls
        return decorator

    def __init__(self, config: dict):
        """
        config = {
            "market_tructure": bool Optinal,
            "FVG": bool Optinal,
            "OBD": bool Optinal,
        }
        """
        self.tools: list[str] = config.get("tools")
        self.params = config.get("params") or {}
        self.instances = {}

    # -----------------------------------
    # Build indicators from config
    # -----------------------------------
    def build(self):
        for name in self.tools or []:
            entry = self._REGISTRY.get(self._normalize_name(name))

            if not entry:
                raise ValueError(f"technical tool: '{name}' not registered")

            cls = entry["cls"]
            try:
                instance = cls(**self.params)
            except TypeError:
                instance = cls.__new__(cls)
            instance.params = dict(self.params or {})
            instance.name = name
            instance._type = entry["type"]
            instance._name = name
            self.instances[name] = instance

        return self.instances


# Import built-in tools for registry side effects.
from advisor.Strategy_model.Fundamentals.tools import fair_value_gap  # noqa: E402,F401
from advisor.Strategy_model.Fundamentals.tools import liquidity_detector  # noqa: E402,F401
from advisor.Strategy_model.Fundamentals.tools import market_structure  # noqa: E402,F401
from advisor.Strategy_model.Fundamentals.tools import order_blocks  # noqa: E402,F401
