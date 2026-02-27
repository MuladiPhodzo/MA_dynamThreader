import logging
import time
from advisor.bootstrap.config_loader import UserConfig, ConfigError
from advisor.bootstrap.state_loader import StateStore
from advisor.Client.mt5Client import MetaTrader5Client

logger = logging.getLogger("BOOTSTRAP")


class BootstrapError(Exception):
    pass


class SystemBootstrap:

    def __init__(self, mt5_client_class: MetaTrader5Client):
        self.mt5_client_class = mt5_client_class

        self.config = None
        self.state = None
        self.client = None

    # ======================================================
    # PUBLIC ENTRY
    # ======================================================

    def initialize(self):

        logger.info("Starting system bootstrap...")

        self._load_user_config()
        self._load_state()
        self._initialize_broker()
        self._verify_account()
        self._sync_account_state()

        logger.info("Bootstrap completed successfully.")

        return {
            "client": self.client,
            "config": self.config,
            "state": self.state
        }

    # ======================================================
    # INTERNAL STEPS
    # ======================================================
    def _load_user_config(self):
        try:
            self.config = UserConfig()
            logger.info("User configuration loaded.")
        except ConfigError as e:
            raise BootstrapError(str(e))

    def _load_state(self):
        self.state = StateStore()
        logger.info("Persistent state loaded.")

    def _initialize_broker(self):

        creds = self.config.creds

        self.client = self.mt5_client_class()
        data = {
            "server": creds["server"],
            "account_id": creds["account_id"],
            "password": creds["password"]
        }

        success = self.mt5_client_class.initialize(data)
 
        if not success:
            raise BootstrapError("Broker connection failed.")

        logger.info("Broker connection successful.")

    def _verify_account(self):

        account_info = getattr(self.client, "account_info", lambda: None)()

        if not account_info:
            raise BootstrapError("Failed to fetch account info.")

        if account_info["balance"] <= 0:
            raise BootstrapError("Account balance invalid.")

        logger.info(
            f"Connected to account {account_info['login']} "
            f"Equity: {account_info['equity']}"
        )

    def _sync_account_state(self):
        try:
            while True:
                account_info = getattr(self.client, "account_info", lambda: None)()

                init_deposit = self.state.get("init_deposit")

                if not init_deposit:
                    self.state.set("init_deposit", account_info["balance"])
                    logger.info("Initial deposit recorded.")

                self.state.set("last_equity", account_info["equity"])

                logger.info("Account state synchronized.")
                time.sleep(60 * 5)  # Sync every 5 minutes
                
        except Exception as e:
            raise BootstrapError(f"Account state sync failed: {e}")
