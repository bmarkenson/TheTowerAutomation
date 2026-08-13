import json
import os
from datetime import datetime

import pytest

from core.single_instance import InstanceAlreadyRunning, SingleInstanceLock
from core.adb_target_session import AdbTargetSession


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
        assert metadata["state"] == "held"

    with second:
        pass


def test_clean_release_clears_owner_and_records_release(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    lock = SingleInstanceLock("localhost:5555", lock_path)

    lock.acquire()
    lock.release()

    metadata = json.loads(lock_path.read_text(encoding="utf-8"))
    assert metadata["target"] == "localhost:5555"
    assert metadata["state"] == "released"
    assert metadata["pid"] is None
    assert datetime.fromisoformat(metadata["released_at"]).tzinfo is not None
    assert "started_at" not in metadata


def test_adb_target_session_handoff_acquires_new_lock_before_releasing_old(
    tmp_path,
    monkeypatch,
):
    def lock_factory(target):
        port = target.rsplit(":", 1)[-1]
        return SingleInstanceLock(target, tmp_path / f"automation-{port}.lock")

    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    session = AdbTargetSession("localhost:5555", lock_factory=lock_factory)
    with session:
        assert session.handoff("localhost:5565", validate=lambda: True)
        assert session.target == "localhost:5565"
        assert os.environ["ADB_DEVICE"] == "localhost:5565"
        with SingleInstanceLock(
            "localhost:5555",
            tmp_path / "automation-5555.lock",
        ):
            pass
        contender = SingleInstanceLock(
            "localhost:5565",
            tmp_path / "automation-5565.lock",
        )
        with pytest.raises(InstanceAlreadyRunning):
            contender.acquire()


def test_adb_target_session_failed_validation_retains_old_target(
    tmp_path,
    monkeypatch,
):
    def lock_factory(target):
        port = target.rsplit(":", 1)[-1]
        return SingleInstanceLock(target, tmp_path / f"automation-{port}.lock")

    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    session = AdbTargetSession("localhost:5555", lock_factory=lock_factory)
    with session:
        assert not session.handoff("localhost:5565", validate=lambda: False)
        assert session.target == "localhost:5555"
        assert os.environ["ADB_DEVICE"] == "localhost:5555"
        with SingleInstanceLock(
            "localhost:5565",
            tmp_path / "automation-5565.lock",
        ):
            pass


def test_adb_target_snapshot_tracks_ownership_handoffs_and_release(
    tmp_path,
    monkeypatch,
):
    def lock_factory(target):
        port = target.rsplit(":", 1)[-1]
        return SingleInstanceLock(target, tmp_path / f"automation-{port}.lock")

    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    session = AdbTargetSession("localhost:5555", lock_factory=lock_factory)
    initial = session.snapshot()
    assert (initial.target, initial.generation, initial.owned) == (
        "localhost:5555",
        0,
        False,
    )

    session.acquire()
    acquired = session.snapshot()
    assert acquired.target == "localhost:5555"
    assert acquired.generation == 1
    assert acquired.owned is True
    acquired_owner = json.loads(
        (tmp_path / "automation-5555.lock").read_text(encoding="utf-8")
    )
    assert acquired_owner["runtime_id"] == session.runtime_id
    assert acquired_owner["target_generation"] == 1
    session.bind_runtime_owner("supervisor-runtime")
    rebound_owner = json.loads(
        (tmp_path / "automation-5555.lock").read_text(encoding="utf-8")
    )
    assert session.runtime_id == "supervisor-runtime"
    assert rebound_owner["runtime_id"] == "supervisor-runtime"
    assert rebound_owner["target_generation"] == 1

    assert not session.handoff("localhost:5565", validate=lambda: False)
    assert session.snapshot() == acquired
    assert session.handoff("localhost:5565", validate=lambda: True)
    handed_off = session.snapshot()
    assert handed_off.target == "localhost:5565"
    assert handed_off.generation == 2
    assert handed_off.owned is True
    handed_off_owner = json.loads(
        (tmp_path / "automation-5565.lock").read_text(encoding="utf-8")
    )
    assert handed_off_owner["runtime_id"] == session.runtime_id
    assert handed_off_owner["target_generation"] == 2

    session.release()
    released = session.snapshot()
    assert released.target == "localhost:5565"
    assert released.generation == 3
    assert released.owned is False
