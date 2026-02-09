from pathlib import Path
from advisor.utils.config_handler import ConfigLoader
from advisor.GUI.userInput import UserGUI as SetupWizard
from advisor.core.state import StateManager


class SystemBootstrap:
    def __init__(self):
        self.config_loader = ConfigLoader(Path("configs.json"))
        self.states = StateManager()

    def _load_setup_cfg(self) -> dict[str, dict]:
        user_cfg = self.config_loader.json_load("user")
        bot_cfg = self.config_loader.json_load("bot")

        if not user_cfg or not bot_cfg:
            return None

        return {"user": user_cfg, "bot": bot_cfg}

    def _run_setup(self, configs: dict):  # data from user gui
        pass

