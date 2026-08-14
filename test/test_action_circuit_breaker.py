from core.action_circuit_breaker import ActionCircuitBreaker


def test_circuit_breaker_blocks_repeated_heartbeats_until_epoch_changes():
    breaker = ActionCircuitBreaker(failure_limit=1)
    epoch = ("runtime-1", "target-1", 7, "scope-1", "NEW_BATTLE")

    assert breaker.allows("home_ad_gem", epoch=epoch) is True
    assert breaker.record_failure(
        "home_ad_gem",
        epoch=epoch,
        reason="target persisted",
    ) is True
    for _ in range(100):
        assert breaker.allows("home_ad_gem", epoch=epoch) is False

    changed = ("runtime-1", "target-1", 7, "scope-2", "NEW_BATTLE")
    assert breaker.allows("home_ad_gem", epoch=changed) is True
    assert breaker.snapshot("home_ad_gem").failures == 0


def test_circuit_breaker_keys_and_success_are_isolated():
    breaker = ActionCircuitBreaker(failure_limit=1)
    epoch = ("scope-1",)
    breaker.record_failure("home_ad_gem", epoch=epoch, reason="persisted")

    assert breaker.allows("home_ad_gem", epoch=epoch) is False
    assert breaker.allows("another_action", epoch=epoch) is True
    assert breaker.record_success("home_ad_gem") is True
    assert breaker.allows("home_ad_gem", epoch=epoch) is True


def test_success_from_old_epoch_cannot_clear_new_epoch_state():
    breaker = ActionCircuitBreaker(failure_limit=1)
    old_epoch = ("scope-1",)
    new_epoch = ("scope-2",)
    breaker.record_failure("home_ad_gem", epoch=old_epoch, reason="persisted")
    assert breaker.allows("home_ad_gem", epoch=new_epoch) is True

    assert breaker.record_success("home_ad_gem", epoch=old_epoch) is False
    assert breaker.snapshot("home_ad_gem").epoch == new_epoch
