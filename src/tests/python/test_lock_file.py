import os
import sys
import tempfile
import unittest

from pathlib import Path

from MA_DynamAdvisor import LOCK_FILE

# Mock version of your lock logic
class TestLockFile(unittest.TestCase):
    """Test lock file creation and removal to ensure single instance enforcement."""

    def create_lock_file(self):
        if os.path.exists(LOCK_FILE):
            raise RuntimeError("Another instance is already running.")
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
        return LOCK_FILE

    def remove_lock_file(self, lock_file):
        if os.path.exists(lock_file):
            os.remove(lock_file)
        return not os.path.exists(lock_file)

    # ----------------------------
    # ✅ TEST CASES
    # ----------------------------

    def test_lock_file_creation_and_removal(self, tmp_path, monkeypatch):
        """Test that lock file is created and removed successfully."""
        self.temp_script = tmp_path / "MA_DynamAdvisor.exe"
        self.temp_script.write_text("fake exe")

        monkeypatch.setattr(sys, "argv", [str(self.temp_script)])

        lock_file = os.path.splitext(os.path.basename(sys.argv[0]))[0] + ".lock"

        # Ensure no lock file exists initially
        assert not os.path.exists(lock_file)

        # Create lock file
        created_file = self.create_lock_file()
        assert os.path.exists(created_file)
        assert created_file == lock_file

        # Remove it
        assert self.remove_lock_file(created_file)
        assert not os.path.exists(created_file)


    def test_prevent_multiple_instances(self, tmp_path, monkeypatch):
        """Test that trying to run a second instance raises an error."""
        self.temp_script = tmp_path / "MA_DynamAdvisor.exe"
        self.temp_script.write_text("fake exe")

        monkeypatch.setattr(sys, "argv", [str(self.temp_script)])

        lock_file = os.path.splitext(os.path.basename(sys.argv[0]))[0] + ".lock"

        # Create first instance
        self.create_lock_file()
        assert os.path.exists(lock_file)

        # Try creating a second one
        with self.assertRaises(RuntimeError, match="Another instance is already running."):
            self.create_lock_file()

        # Clean up
        self.remove_lock_file(lock_file)
        assert not os.path.exists(lock_file)


    def test_remove_lock_file_if_not_exists(self, tmp_path):
        """Ensure remove_lock_file() handles missing files gracefully."""
        missing_file = tmp_path / "nonexistent.lock"
        assert self.remove_lock_file(missing_file)  # Should not raise error
        assert not os.path.exists(missing_file)
        
if __name__ == "__main__":
    unittest.main()
