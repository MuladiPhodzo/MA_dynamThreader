from advisor.Client.mt5Client import MetaTrader5Client
from advisor.bootstrap.config_loader import ConfigError, UserConfig
from advisor.bootstrap.state_loader import StateStore
from advisor.utils.logging_setup import get_logger

logger = get_logger("BOOTSTRAP")


class BootstrapError(Exception):
    pass


class SystemBootstrap:
    def __init__(self, mt5_client_class=MetaTrader5Client):
        self.mt5_client_class = mt5_client_class
        self.config = None
        self.state = None
        self.client = None

    def initialize(self):
        self._load_user_config()
        self._load_state()
        # self._initialize_broker()
        # self._verify_account()
        # self._sync_account_state_once()
        return {"client": self.client, "config": self.config, "state": self.state}

    def _load_user_config(self):
        try:
            self.config = UserConfig()
        except ConfigError as e:
            raise BootstrapError(str(e))

    def _load_state(self):
        self.state = StateStore()

    def _initialize_broker(self):
        creds = self.config.creds
        self.client = self.mt5_client_class()
        data = {
            "server": creds["server"],
            "account_id": creds["account_id"],
            "password": creds["password"],
        }
        success = self.client.initialize(data)
        if not success:
            raise BootstrapError("Broker connection failed.")

    def _verify_account(self):
        info = getattr(self.client, "account_info", None)
        if not info:
            raise BootstrapError("Failed to fetch account info.")
        if info.get("balance", 0) <= 0:
            raise BootstrapError("Account balance invalid.")

    def _sync_account_state_once(self):
        account_info = getattr(self.client, "account_info", None)
        if not account_info:
            return

        init_deposit = self.state.get("init_deposit")
        if not init_deposit:
            self.state.set("init_deposit", account_info.get("balance"))
        self.state.set("last_equity", account_info.get("equity"))
