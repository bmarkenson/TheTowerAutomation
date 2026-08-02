from datetime import datetime, timedelta, timezone
import json
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from core.app import App
from core.daily_gem_scheduler import (
    DailyGemScheduler,
    FAILURE_RETRY_DELAY,
    ROLLOVER_GRACE,
)
from handlers.daily_gem_handler import DailyGemResult


UTC = timezone.utc
PACIFIC = ZoneInfo("America/Los_Angeles")


def test_utc_rollover_is_5pm_pdt_and_4pm_pst(tmp_path):
    summer = DailyGemScheduler(tmp_path / "summer.json")
    winter = DailyGemScheduler(tmp_path / "winter.json")

    summer_local = datetime(2026, 7, 13, 17, 1, tzinfo=PACIFIC)
    winter_local = datetime(2026, 1, 13, 16, 1, tzinfo=PACIFIC)

    assert summer_local.astimezone(UTC).hour == 0
    assert winter_local.astimezone(UTC).hour == 0
    assert summer.should_attempt(now=summer_local)
    assert winter.should_attempt(now=winter_local)


def test_scheduler_waits_for_post_midnight_grace(tmp_path):
    scheduler = DailyGemScheduler(tmp_path / "state.json")
    midnight = datetime(2026, 7, 14, tzinfo=UTC)

    assert not scheduler.should_attempt(now=midnight)
    assert not scheduler.should_attempt(now=midnight + ROLLOVER_GRACE - timedelta(seconds=1))
    assert scheduler.should_attempt(now=midnight + ROLLOVER_GRACE)


def test_visible_badge_can_trigger_before_rollover_grace(tmp_path):
    scheduler = DailyGemScheduler(tmp_path / "state.json")
    midnight = datetime(2026, 7, 14, tzinfo=UTC)

    assert scheduler.should_attempt(badge_visible=True, now=midnight)


def test_completion_persists_across_process_restart(tmp_path):
    state_path = tmp_path / "state.json"
    now = datetime(2026, 7, 14, 0, 2, tzinfo=UTC)
    scheduler = DailyGemScheduler(state_path)

    scheduler.mark_completed(DailyGemResult.CLAIMED.value, now=now)

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["completed_utc_day"] == "2026-07-14"
    restarted = DailyGemScheduler(state_path)
    assert not restarted.should_attempt(badge_visible=True, now=now)
    assert restarted.should_attempt(now=now + timedelta(days=1))


def test_failure_retries_after_backoff_without_consuming_day(tmp_path):
    scheduler = DailyGemScheduler(tmp_path / "state.json")
    now = datetime(2026, 7, 14, 0, 2, tzinfo=UTC)

    scheduler.mark_failed(now=now)

    assert not scheduler.should_attempt(now=now + FAILURE_RETRY_DELAY - timedelta(seconds=1))
    assert scheduler.should_attempt(now=now + FAILURE_RETRY_DELAY)


def test_app_dispatches_scheduled_probe_without_badge_and_records_result():
    app = App.__new__(App)
    app._daily_gem_scheduler = Mock()
    app._daily_gem_scheduler.should_attempt.return_value = True
    app._blind_tapper_suspended = False
    app._authority_battle_active = True
    app._authority_primary_state = "RUNNING"
    app._authority_holds = ()
    app._supervisor = Mock(is_paused=False)
    app._supervisor.apply_control.return_value = False
    app._status_reporter = Mock()

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=True),
        patch("core.app.handle_daily_gem", return_value=DailyGemResult.CLAIMED) as handler,
    ):
        assert app._handle_daily_gem_if_due("RUNNING", set())

    attempt = app._daily_gem_scheduler.should_attempt.call_args.kwargs
    assert attempt["badge_visible"] is False
    assert attempt["now"].tzinfo is UTC
    handler.assert_called_once()
    assert callable(handler.call_args.kwargs["action_guard_fn"])
    assert callable(handler.call_args.kwargs["route_state_callback"])
    app._daily_gem_scheduler.mark_completed.assert_called_once_with(
        "claimed",
        now=attempt["now"],
    )
    app._daily_gem_scheduler.mark_failed.assert_not_called()
    assert app._blind_tapper_suspended


def test_app_defers_scheduled_probe_from_unsafe_screen():
    app = App.__new__(App)
    app._daily_gem_scheduler = Mock()

    assert not app._handle_daily_gem_if_due("HOME_SCREEN", set())
    assert not app._handle_daily_gem_if_due("STORE", set())
    app._daily_gem_scheduler.should_attempt.assert_not_called()
