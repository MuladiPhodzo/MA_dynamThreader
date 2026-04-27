class IndicatorRegistry:
    """
    Dynamic indicator loader + executor
    """
    _REGISTRY = {}

    @classmethod
    def register(cls, name: str, type: str):
        def decorator(indicator_cls):
            cls._REGISTRY[name.lower()] = {"type": type.lower(),
                                           "cls": indicator_cls}
            return indicator_cls
        return decorator

    def __init__(self, config: dict):
        """
        config = {
            "ma": {...},
            "macd": {...},
            "ao": {...}
        }
        """
        self.config = config.get("params") or {}
        self.instances = {}
        self.execution_order = []

    # -----------------------------------
    # Build indicators from config
    # -----------------------------------
    def build(self):
        for name, params in self.config.items():
            entry = self._REGISTRY.get(name.lower())
            if not entry:
                raise ValueError(f"Indicator '{name}' not registered")

            cls = entry["cls"]
            indicator_type = entry["type"]

            try:
                instance = cls(**params)
            except TypeError:
                instance = cls.__new__(cls)
            instance.params = dict(params or {})
            instance.name = name
            instance._type = indicator_type   # attach type
            instance._name = name

            self.instances[name] = instance
        return self.instances

def load_builtin_indicators() -> None:
    """Import bundled indicators so decorator registrations are available."""
    from advisor.Strategy_model.indicators.ATR import ATR  # noqa: F401
    from advisor.Strategy_model.indicators.Awesome_Ascillator import awesome_ascillator  # noqa: F401
    from advisor.Strategy_model.indicators.MA import MovingAverage  # noqa: F401
    from advisor.Strategy_model.indicators.MACD import MACD  # noqa: F401
    from advisor.Strategy_model.indicators.RSI import RSI  # noqa: F401
