import json

import pytest

from core.single_instance import InstanceAlreadyRunning, SingleInstanceLock


def test_second_lock_for_same_target_is_rejected(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    first = SingleInstanceLock("localhost:5555", lock_path)
    second = SingleInstanceLock("localhost:5555", lock_path)

    first.acquire()
    try:
        with pytest.raises(InstanceAlreadyRunning, match="localhost:5555"):
            second.acquire()
    finally:
        first.release()


def test_lock_can_be_reacquired_after_release(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    first = SingleInstanceLock("localhost:5555", lock_path)
    second = SingleInstanceLock("localhost:5555", lock_path)

    with first:
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert metadata["target"] == "localhost:5555"
        assert isinstance(metadata["pid"], int)

    with second:
        pass
