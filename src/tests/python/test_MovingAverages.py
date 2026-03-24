from multiprocessing import Manager

from advisor.Trade.RiskManager import RiskManager
from advisor.Trade.trateState import TradeStateManager
from advisor.core.health_bus import HealthBus
from advisor.core.state import StateManager


class DummyClient:
    def __init__(self, equity=10_000):
        self._equity = equity

    def get_equity(self):
        return self._equity


class DummySignal:
    symbol = "EURUSD"
    sl = 20


def test_risk_manager_allows_valid_trade():
    manager = Manager()
    client = DummyClient()
    risk = RiskManager(
        client=client,
        trade_state=TradeStateManager(client),
        state_manager=StateManager(),
        health_bus=HealthBus(manager),
    )

    allowed, lot = risk.validate(DummySignal())
    assert allowed is True
    assert lot > 0
