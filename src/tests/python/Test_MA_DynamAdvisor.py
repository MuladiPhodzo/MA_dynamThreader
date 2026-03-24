from advisor.__main__ import ensure_single_instance


def test_lock_file_roundtrip(tmp_path):
    lock_file = tmp_path / "MA_DynamAdvisor.lock"

    assert ensure_single_instance(lock_file) is True
    assert lock_file.exists()

    # Second attempt should fail while the current PID is alive.
    assert ensure_single_instance(lock_file) is False

    lock_file.unlink()
