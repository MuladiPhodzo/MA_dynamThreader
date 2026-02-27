import json
from pathlib import Path
from unittest.mock import patch

from advisor.Telegram.utils import singleton


def test_check_and_create_lock(tmp_path, monkeypatch):
    lock = tmp_path / "MA_DynamAdvisor.lock"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(singleton, "LOCK_FILE", str(lock))

    with patch("advisor.Telegram.utils.singleton.psutil.pid_exists", return_value=False):
        lock.write_text(json.dumps({"pid": 999999, "timestamp": 0}), encoding="utf-8")
        assert singleton.check_and_create_lock() is True

    payload = json.loads(Path(lock).read_text(encoding="utf-8"))
    assert "pid" in payload
    singleton.cleanup_lock()
    assert not lock.exists()
