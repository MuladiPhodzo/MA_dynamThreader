import json
import os


class ConfigError(Exception):
    pass


class UserConfig:

    def __init__(self, path="user.json"):
        self.path = path
        self.data = self._load()
        self._validate()

    def _load(self):
        if not os.path.exists(self.path):
            raise ConfigError(f"{self.path} not found.")

        with open(self.path, "r") as f:
            return json.load(f)

    def _validate(self):
        required = ["creds", "trade_configs", "account_data"]

        for key in required:
            if key not in self.data:
                raise ConfigError(f"Missing config section: {key}")

        creds = self.data["creds"]
        for field in ["server", "account_id", "password"]:
            if field not in creds:
                raise ConfigError(f"Missing credential field: {field}")

    @property
    def creds(self):
        return self.data["creds"]

    @property
    def trade(self):
        return self.data["trade_configs"]

    @property
    def account(self):
        return self.data["account_data"]
