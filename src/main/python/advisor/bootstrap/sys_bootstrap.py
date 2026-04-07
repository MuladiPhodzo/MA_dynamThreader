from multiprocessing.managers import SyncManager

from advisor.bootstrap.config_loader import ConfigError, UserConfig
from advisor.utils.logging_setup import get_logger
from advisor.core.state import StateManager

logger = get_logger("BOOTSTRAP")


class BootstrapError(Exception):
    pass


class SystemBootstrap:
    def __init__(self, manager: SyncManager):
        self.manager = manager
        self.config = None
        self.state = None

    def initialize(self) -> UserConfig | None:
        """_summary_
            loads config files and syncing saved states for a new run
        Returns:
            UserConfig | None: a user configeration data class loaded from a file
        """        
        self._load_user_config()
        self._load_state()
        return self.config

    def _load_user_config(self):
        try:
            logger.info("loading user configs")
            self.config = UserConfig()
        except ConfigError as e:
            raise BootstrapError(str(e))

    def _load_state(self):
        logger.info("loading saved bot state")
        self.state = StateManager(self.manager)

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
