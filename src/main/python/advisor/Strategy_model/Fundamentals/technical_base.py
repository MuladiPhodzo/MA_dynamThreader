class Technical:
    def __init__(self, name: str, **params):
        self.name = name
        self.params = params or {}

    def compute(self, df):
        raise NotImplementedError
