import os
import sys
import subprocess
import time
import signal
import unittest
from pathlib import Path
import tempfile


class BotProcess:
    """Helper class to manage the bot subprocess for integration testing."""

    def __init__(self, script_path: Path):
        self.script_path = script_path
        self.process = None

    def start(self):
        """Launch the bot subprocess."""
        self.process = subprocess.Popen(
            [sys.executable, str(self.script_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return self.process

    def stop(self):
        """Attempt graceful stop, then force kill if needed."""
        if not self.process:
            return
        self.process.send_signal(signal.SIGINT)
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()


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
        print("Another instance is already running.")
        sys.exit(1)

    open(LOCK_FILE, "w").close()
    print("Lock file created.")

    # Simulate long-running bot
    time.sleep(10)

finally:
    if os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)
        print("Lock file removed.")
"""
        self.script_path.write_text(script_code)

    def tearDown(self):
        """Clean up temp directory after test."""
        self.temp_dir.cleanup()

    def test_lock_file_lifecycle(self):
        """Ensure the lock file is created when bot starts and removed when stopped."""
        bot = BotProcess(self.script_path)

        # Start the simulated bot
        bot.start()
        time.sleep(2)  # Give the process time to start

        # Check that the lock file was created
        self.assertTrue(self.lock_file.exists(), "❌ Lock file was not created.")

        # Stop the bot process
        bot.stop()
        time.sleep(1)  # Allow cleanup time

        # Verify lock file cleanup
        self.assertFalse(self.lock_file.exists(), "❌ Lock file still exists after bot stopped.")

        # Read output for debugging
        stdout, stderr = bot.process.communicate()
        print(stdout.decode())
        print(stderr.decode())


if __name__ == "__main__":
    unittest.main()
