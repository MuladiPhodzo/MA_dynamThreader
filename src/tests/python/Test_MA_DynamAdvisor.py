import os
import sys
import subprocess
import signal
import unittest
from pathlib import Path
import tempfile


class BotProcess:
    def __init__(self, script_path):
        self.script_path = script_path
        self.process = None

    def start(self):
        self.process = subprocess.Popen(
            [sys.executable, self.script_path],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )

    def stop(self):
        if self.process and self.process.poll() is None:
            if os.name == "nt":
                os.kill(self.process.pid, signal.CTRL_BREAK_EVENT)
            else:
                self.process.send_signal(signal.SIGINT)
            self.process.wait(timeout=5)


class TestLockFileIntegration(unittest.TestCase):
    """Integration test to ensure lock file is created and removed properly."""

    def setUp(self):
        """Create a temporary bot script simulating the real executable."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.lock_file = Path(self.temp_dir.name) / "MA_DynamAdvisor.lock"
        self.script_path = Path(self.temp_dir.name) / "MA_DynamAdvisor.py"

        script_code = f"""
            import os, sys, time

            LOCK_FILE = r"{self.lock_file}"

            try:
                if os.path.exists(LOCK_FILE):
                    logger.info("Another instance is already running.")
                    sys.exit(1)

                open(LOCK_FILE, "w").close()
                logger.info("Lock file created.")

                # Simulate long-running bot
                time.sleep(10)

            finally:
                if os.path.exists(LOCK_FILE):
                    os.remove(LOCK_FILE)
                    logger.info("Lock file removed.")
            """
        self.script_path.write_text(script_code)

    def tearDown(self):
        """Clean up temp directory after test."""
        self.temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
