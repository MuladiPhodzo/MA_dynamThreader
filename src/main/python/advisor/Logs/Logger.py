import sys
import csv
import os
from datetime import datetime

class FileLogger:
    def __init__(self, user_data, filename=None):
        self.user_data = user_data

        # Dynamically resolve the user's Documents folder
        documents_dir = os.path.join(os.path.expanduser("~"), "Documents")
        logs_dir = os.path.join(documents_dir, "TradingBotLogs")
        os.makedirs(logs_dir, exist_ok=True)

        self.filename = filename or os.path.join(logs_dir, "logs.csv")
        self._init_file()
        
        # Redirect print() to this logger
        sys.stdout = self
        sys.stderr = self
        sys.excepthook = self

    def _init_file(self):
        is_new = not os.path.exists(self.filename)
        if is_new:
            with open(self.filename, mode='w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([])
            
    def write(self, message: str):
        
        message = message.strip()
        if not message:
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        account_info = f"{timestamp} »»» {self.user_data.get('server')}-{self.user_data.get('account_id')}"
        log_entry = f": {message}"

        with open(self.filename, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([account_info, log_entry])

    def flush(self):
        pass
