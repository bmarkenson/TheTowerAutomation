from __future__ import annotations

"""Primary application orchestration loop for the automation runtime."""

import threading
import time
from typing import Optional, Dict, Any, Set, Tuple

import numpy as np
from numpy.typing import NDArray

from utils.logger import log, set_mission_log_path
from core.watchdog import watchdog_process_check, ensure_adb_connected
from core.ss_capture import capture_and_save_screenshot
from core.state_detector import detect_state_and_overlays
from core.automation_supervisor import AutomationSupervisor
from core.run_state import AUTOMATION, ExecMode
from core.app_setup import AppConfig
from core.status_report import StateChangeTracker, StatusReporter
from core.recovery import handle_unknown_state, update_unknown_state
from automation.missions.manager import MissionManager
from automation.missions import get_mission
from automation.missions.yaml_mission import YamlMission
from automation.strategies import get_strategy
from handlers.game_over_handler import handle_game_over
from handlers.home_screen_handler import handle_home_screen
from handlers.ad_gem_handler import handle_ad_gem, stop_blind_gem_tapper, start_blind_gem_tapper
from handlers.daily_gem_handler import handle_daily_gem
from handlers.dismiss_uw_detail import handle_uw_detail_popup
from utils.wave_detector import detect_wave_number_from_image


Frame = NDArray[np.uint8]


class App:
    """Main automation orchestrator wrapping capture → detect → dispatch."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        set_mission_log_path(config.mission_log_path)
        self._supervisor = AutomationSupervisor(
            control_file=config.control_file,
            auto_resume_secs=config.auto_resume_secs,
            auto_resume_enabled=config.auto_resume_enabled,
            auto_return_secs=config.auto_return_secs,
            auto_return_enabled=config.auto_return_enabled,
            auto_return_conf_threshold=config.auto_return_conf_threshold,
            coins_toggle_cooldown=config.coins_toggle_cooldown,
            coins_conf_floor=config.coins_conf_floor,
            coins_max_jump_factor=config.coins_max_jump_factor,
            coins_jump_conf_floor=config.coins_jump_conf_floor,
        )
        self._supervisor.schedule_total_snapshot("startup")

        self._mission_mgr = MissionManager(self._load_mission(config), self._load_strategy(config))
        self._mission_mgr.start()

        self._state_tracker = StateChangeTracker()
        self._status_reporter = StatusReporter(
            interval_secs=config.status_interval,
            supervisor=self._supervisor,
            save_wave_samples=config.save_wave_samples,
            save_coin_samples=config.save_coin_samples,
            coins_log_base=config.coins_log_base,
            coins_log_enabled=config.coins_log_enabled,
        )

        self._match_trace = config.match_trace
        self._auto_start_enabled = config.auto_start_enabled

        self._mission_active = bool(
            (config.mission_name and config.mission_name.lower() != "none")
            or config.mission_config_path
        )
        self._fast_game_over = config.fast_game_over or (self._mission_active and not config.full_game_over)

        self._last_wave_value: Optional[int] = None
        self._last_wave_conf: float = -1.0
        self._last_wave_ts: float = 0.0
        self._blind_tapper_suspended = False
        self._run_initialization_gate_logged = False

    def run(self) -> None:
        log("Starting main heartbeat loop.", level="INFO", console=True)
        if ensure_adb_connected():
            time.sleep(2)

        threading.Thread(target=watchdog_process_check, daemon=True).start()

        if self._config.wait_on_start:
            try:
                AUTOMATION.mode = ExecMode.WAIT
                log("[CTRL] Startup flag: ExecMode set to WAIT", "INFO", console=True)
            except Exception:
                pass

        try:
            while True:
                img = self._capture_frame()
                if img is None:
                    continue

                self._supervisor.apply_control()
                is_paused = self._supervisor.is_paused

                detection = detect_state_and_overlays(img, log_matches=self._match_trace)

                # Establish the new-run boundary from the minimum RUNNING
                # detection, then give an initializing strategy exclusive tap
                # authority. No overlay handler, recovery tap, mission action,
                # or blind tapper may run before this gate clears.
                self._mission_mgr.maybe_run_start(detection)
                initialization_pending = self._mission_mgr.run_initialization_pending(detection)
                if initialization_pending:
                    if not self._run_initialization_gate_logged:
                        log(
                            "[RUN_INIT] Exclusive startup gate active; normal handlers are blocked",
                            "INFO",
                            console=True,
                        )
                        self._run_initialization_gate_logged = True
                    if stop_blind_gem_tapper():
                        self._blind_tapper_suspended = True
                    if not is_paused:
                        self._mission_mgr.tick(img, detection, strategy_only=True)
                    mv = self._mission_mgr.ctx.data.setdefault("mission_vars", {})
                    sleep_interval = max(
                        0.5,
                        float(mv.get("loop_sleep_override_sec") or 1.0),
                    )
                    time.sleep(sleep_interval)
                    continue
                if self._run_initialization_gate_logged:
                    log(
                        "[RUN_INIT] Startup gate complete; normal handlers may resume",
                        "INFO",
                        console=True,
                    )
                    self._run_initialization_gate_logged = False

                img, detection, overlay_cleared = self._resolve_uw_detail_overlay(img, detection)
                if not overlay_cleared:
                    time.sleep(0.3)
                    continue

                wave_val: Optional[int] = None
                wave_conf: float = -1.0
                if detection.get("state") == "RUNNING":
                    try:
                        wave_val, wave_conf = detect_wave_number_from_image(img)
                    except Exception:
                        wave_val, wave_conf = None, -1.0

                if wave_val is not None:
                    self._last_wave_value = wave_val
                    self._last_wave_conf = wave_conf
                    self._last_wave_ts = time.time()
                else:
                    wave_val = self._last_wave_value
                    wave_conf = self._last_wave_conf

                detection["wave"] = wave_val
                detection["wave_conf"] = wave_conf
                mv = self._mission_mgr.ctx.data.setdefault("mission_vars", {})
                mv["last_wave"] = wave_val
                mv["last_wave_conf"] = wave_conf
                mv["last_wave_ts"] = self._last_wave_ts

                new_state, menu, secondary, overlays = self._normalise_detection(detection)

                # Allow missions to react immediately to overlays before general state handling.
                self._mission_mgr.handle_overlays(detection)

                if "TOURNAMENT" in secondary:
                    try:
                        if AUTOMATION.mode != ExecMode.WAIT:
                            AUTOMATION.mode = ExecMode.WAIT
                            log("[CTRL] Tournament detected — ExecMode set to WAIT", "INFO")
                    except Exception:
                        pass

                self._mission_mgr.on_state(detection)

                self._state_tracker.update(state=new_state, menu=menu, secondary=secondary, overlays=overlays)

                self._status_reporter.maybe_report(
                    img=img,
                    ui_state=new_state,
                    menu=menu,
                    secondary=secondary,
                    overlays=overlays,
                )

                self._supervisor.auto_return_check(img, new_state)

                if new_state == "UNKNOWN":
                    update_unknown_state(True)
                    trigger_after = self._supervisor.auto_return_secs or 900
                    handle_unknown_state(img, trigger_after_s=trigger_after)
                else:
                    update_unknown_state(False)

                if new_state != "RUNNING":
                    if stop_blind_gem_tapper():
                        self._blind_tapper_suspended = True
                else:
                    if self._blind_tapper_suspended:
                        start_blind_gem_tapper(duration=10, interval=1, blocking=False)
                        self._blind_tapper_suspended = False

                if not is_paused:
                    self._mission_mgr.tick(img, detection)
                    self._handle_primary_states(new_state, overlays)

                sleep_interval = 5.0
                try:
                    override = float(mv.get("loop_sleep_override_sec") or 0.0)
                    if override > 0:
                        sleep_interval = max(0.5, override)
                except Exception:
                    sleep_interval = 5.0
                time.sleep(sleep_interval)
        except KeyboardInterrupt:
            log("KeyboardInterrupt — shutting down.", "INFO")
        finally:
            stop_blind_gem_tapper()
            log("Exited cleanly.", "INFO")

    def _capture_frame(self) -> Optional[Frame]:
        """Capture a new frame from the device, retrying once if ADB reconnects."""
        img = capture_and_save_screenshot(log_capture=False)
        if img is None:
            if ensure_adb_connected():
                time.sleep(1)
                img = capture_and_save_screenshot(log_capture=False)
            if img is None:
                log("Failed to capture screenshot.", level="FAIL")
                time.sleep(2)
                return None
        return img

    def _handle_primary_states(self, new_state: str, overlays: Set[str]) -> None:
        """Dispatch handlers for top-level UI states and overlay-driven events."""
        if new_state == "GAME_OVER":
            log("Detected GAME_OVER. Executing handler.", "INFO", console=True)
            handle_game_over(capture_stats=(not self._fast_game_over))
            self._mission_mgr.on_game_over()
            self._supervisor.record_run_restart()
            new_path = self._status_reporter.rotate_coins_log()
            if new_path:
                log(f"[COINS] Started new coins log: {new_path}", "INFO")
        elif new_state == "HOME_SCREEN":
            log("Detected HOME_SCREEN. Executing handler.", "INFO")
            handle_home_screen(restart_enabled=self._auto_start_enabled)
            self._mission_mgr.on_home()

        if "AD_GEMS_AVAILABLE" in overlays:
            handle_ad_gem()
        if "DAILY_GEMS_AVAILABLE" in overlays:
            handle_daily_gem()

    def _normalise_detection(self, detection: Dict[str, Any]) -> tuple[str, Optional[str], Set[str], Set[str]]:
        """Normalise detector output, ensuring deterministic container types."""
        state = detection.get("state") or "UNKNOWN"
        menu = detection.get("menu") or None
        secondary = set(detection.get("secondary_states") or [])
        overlays = set(detection.get("overlays") or [])
        return state, menu, secondary, overlays

    def _resolve_uw_detail_overlay(
        self,
        img: Frame,
        detection: Dict[str, Any],
        *,
        max_attempts: int = 3,
    ) -> Tuple[Frame, Dict[str, Any], bool]:
        overlays = set(detection.get("overlays") or [])
        if "UW_DETAIL" not in overlays:
            return img, detection, True

        for attempt in range(1, max_attempts + 1):
            handled_image = handle_uw_detail_popup(screenshot=img)
            if handled_image is not None:
                img = handled_image
            time.sleep(0.2)
            detection = detect_state_and_overlays(img, log_matches=self._match_trace)
            overlays = set(detection.get("overlays") or [])
            if "UW_DETAIL" not in overlays:
                log(
                    f"[UW_DETAIL] Overlay cleared after attempt {attempt}",
                    "DEBUG",
                )
                return img, detection, True

        log(
            "[UW_DETAIL] Overlay persisted after multiple attempts; will retry next loop",
            "WARN",
        )
        return img, detection, False

    def _load_mission(self, config: AppConfig):
        """Initialise the mission configuration based on CLI options."""
        if config.mission_config_path:
            try:
                mission = YamlMission.from_file(config.mission_config_path)
                log(
                    f"[MISSION] Loaded YAML mission from {config.mission_config_path}",
                    "INFO",
                    console=True,
                )
                return mission
            except Exception as exc:
                log(f"[MISSION] Failed to load YAML mission: {exc}", "ERROR")
        return get_mission(config.mission_name)

    def _load_strategy(self, config: AppConfig):
        """Initialise the strategy configuration (YAML overrides name)."""
        if config.strategy_config_path:
            try:
                from automation.strategies.yaml_strategy import YamlStrategy

                strat = YamlStrategy.from_file(config.strategy_config_path)
                log(
                    f"[STRATEGY] Loaded YAML strategy from {config.strategy_config_path}",
                    "INFO",
                    console=True,
                )
                return strat
            except Exception as exc:
                log(f"[STRATEGY] Failed to load YAML strategy: {exc}", "ERROR")
        return get_strategy(config.strategy_name)


__all__ = ["App"]
