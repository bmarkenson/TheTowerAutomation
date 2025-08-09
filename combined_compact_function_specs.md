# automation/run_demon_mode.py.md

$PROJECT_ROOT/automation/run_demon_mode.py — Entrypoint
automation.run_demon_mode.main(delay=2, once=False) — Returns: action result (mission loop calling handlers.mission_demon_mode.run_demon_mode(); exits after one iteration when once=True); Side effects: [loop] [log] [adb]; Errors: KeyboardInterrupt stops loop; unhandled exceptions are logged and the loop retries after delay.


# automation/run_demon_nuke.py.md

$PROJECT_ROOT/automation/run_demon_nuke.py — Entrypoint
automation.run_demon_nuke.main() — Returns: None; runs a persistent mission loop that calls handlers.mission_demon_nuke.run_demon_nuke_strategy() each iteration with a 2s delay; Side effects: [log][loop]; Errors: KeyboardInterrupt stops loop; other exceptions logged and retried after 2s


# core/adb_utils.py.md

$PROJECT_ROOT/core/adb_utils.py — Library
core.adb_utils.adb_shell(cmd, capture_output=False, check=True, device_id=None) — Returns: subprocess.CompletedProcess (stdout in .stdout when capture_output=True; otherwise output discarded); Side effects: [adb]; Errors: Returns None on CalledProcessError or unexpected Exception (error text printed).
core.adb_utils.screencap_png(device_id=None, check=True) — Returns: PNG bytes from connected device/emulator (or None on failure); Side effects: [adb]; Errors: Returns None when ADB capture fails or returns invalid data; stderr printed on error.


# core/automation_state.py.md

$PROJECT_ROOT/core/automation_state.py — Library
core.automation_state.AutomationControl — Class: thread-safe holder for run state and execution mode
core.automation_state.AutomationControl.state (property) — Returns: RunState enum; Side effects: [state] when set (validated & locked); Errors: ValueError on invalid string; TypeError on wrong type
core.automation_state.AutomationControl.mode (property) — Returns: ExecMode enum; Side effects: [state] when set (validated & locked); Errors: ValueError on invalid string; TypeError on wrong type
core.automation_state.RunState — Class: Enum of run states {"RUNNING","PAUSED","STOPPED","UNKNOWN"}
core.automation_state.ExecMode — Class: Enum of execution modes {"RETRY","WAIT","HOME"}


# core/clickmap_access.py.md

$PROJECT_ROOT/core/clickmap_access.py — Library
core.clickmap_access.get_clickmap() — Returns: in-memory clickmap dict (mutable reference).
core.clickmap_access.get_clickmap_path() — Returns: absolute path to clickmap.json (str).
core.clickmap_access.resolve_dot_path(dot_path: str, data: Optional[Mapping[str, Any]] = None) — Returns: value at dot path in provided mapping or global clickmap; None if missing.
core.clickmap_access.dot_path_exists(dot_path: str, data: Optional[Mapping[str, Any]] = None) — Returns: True if resolve_dot_path() yields non-None; False otherwise.
core.clickmap_access.set_dot_path(dot_path: str, value: Any, allow_overwrite: bool = False) — Returns: None (mutates in-memory clickmap); Errors: KeyError if final key exists and allow_overwrite=False; ValueError if path traverses non-dict.
core.clickmap_access.interactive_get_dot_path(clickmap: Dict[str, Any]) — Returns: 'group.suffix' or 'upgrades.<attack|defense|utility>.<left|right>'; None if user cancels; Side effects: [fs?] none; interactive I/O.
core.clickmap_access.prompt_roles(group: str, key: str) — Returns: list[str] role suggestions (interactive override allowed).
core.clickmap_access.get_click(name: str) — Returns: (x:int, y:int) from explicit 'tap' or center of 'match_region'; None if unresolved.
core.clickmap_access.get_swipe(name: str) — Returns: swipe dict {x1,y1,x2,y2,duration_ms} or None.
core.clickmap_access.has_click(name: str) — Returns: bool indicating click coords resolvable.
core.clickmap_access.tap_now(name: str) — Returns: None (issues ADB tap); Side effects: [adb], [log]; Errors: CalledProcessError if adb_shell fails (when check=True upstream).
core.clickmap_access.swipe_now(name: str) — Returns: None (issues ADB swipe); Side effects: [adb], [log]; Errors: CalledProcessError if adb_shell fails (when check=True upstream).
core.clickmap_access.save_clickmap(data: Optional[Dict[str, Any]] = None) — Returns: None (atomic JSON write to clickmap.json UTF-8); Side effects: [fs].
core.clickmap_access.flatten_clickmap(data: Optional[Dict[str, Any]] = None, prefix: str = "") — Returns: flat dict mapping dot paths → leaf values.
core.clickmap_access.get_entries_by_role(role: str) — Returns: dict of entries whose 'roles' include role (dot-path → entry dict).


# core/floating_button_detector.py.md

$PROJECT_ROOT/core/floating_button_detector.py — Library
core.floating_button_detector.tap_floating_button(name, buttons) — Returns: True if the named floating_button was tapped; False if not found; Side effects: [adb][log]; Errors: CalledProcessError when ADB command fails (via adb_shell).
core.floating_button_detector.detect_floating_buttons(screen) — Returns: list of detected buttons with {name, match_region, confidence, tap_point}; Side effects: [cv2][fs][state][log]; Errors: Per-entry exceptions are caught and logged; function returns partial results.


# core/label_tapper.py.md

$PROJECT_ROOT/core/label_tapper.py — Library
core.label_tapper.resolve_region(entry, clickmap) — Returns: region dict {x,y,w,h} from entry.match_region or shared region_ref; Errors: ValueError when region_ref unknown or no region defined.
core.label_tapper.get_label_match(label_key, screenshot=None, return_meta=False) — Returns: (x,y,w,h) match in screen coords (or dict with metadata when return_meta=True); Side effects: [adb] when screenshot is captured; Errors: ValueError when label missing, region out of bounds, or match below threshold (default 0.90); FileNotFoundError when template missing; RuntimeError when screenshot capture fails. [cv2] [state]
core.label_tapper.tap_label_now(label_key) — Returns: True on tap injected, False on match/capture/template failure; Side effects: [tap] [adb] [log]; Errors: non-material to callers (handled internally).
core.label_tapper.is_visible(label_key, screenshot=None) — Returns: True if label matches threshold (default 0.90), else False; Side effects: [adb] when screenshot is captured; Errors: non-material (internal ValueError handling). [cv2] [state]


# core/ss_capture.py.md

$PROJECT_ROOT/core/ss_capture.py — Library|Entrypoint
core.ss_capture.capture_adb_screenshot() — Returns: OpenCV BGR ndarray of current device/emulator screen (or None on failure); Side effects: [adb][cv2][log]; Errors: Returns None when PNG capture or decode fails; logs errors via utils.logger.log.
core.ss_capture.capture_and_save_screenshot(path=LATEST_SCREENSHOT) — Returns: same image ndarray as capture_adb_screenshot (or None); Side effects: [adb][cv2][fs][log]; Defaults: saves to screenshots/latest.png; Errors: Returns None if capture fails; creates parent directories when saving.
core.ss_capture.main() — Returns: action result (UI preview only); Side effects: [adb][cv2][log]; Displays captured screenshot in a preview window when run as a script.


# core/state_detector.py.md

$PROJECT_ROOT/core/state_detector.py — Library
core.state_detector.load_state_definitions() — Returns: dict parsed from config/state_definitions.yaml; Side effects: [fs]; Errors: FileNotFoundError/PermissionError; yaml.YAMLError on malformed YAML
core.state_detector.detect_state_and_overlays(screen) — Returns: {"state": str, "secondary_states": [str], "overlays": [str]} chosen by matching clickmap keys via template matching; Side effects: [cv2], [state], [log]; Errors: RuntimeError when multiple primary states match


# core/tap_dispatcher.py.md

$PROJECT_ROOT/core/tap_dispatcher.py — Library
core.tap_dispatcher.log_tap(x, y, label) — Returns: None; Side effects: [log]
core.tap_dispatcher.tap(x, y, label=None) — Returns: enqueues a device tap to be executed by the background worker thread; Side effects: [tap], [log]
core.tap_dispatcher.main() — Returns: None; Side effects: [loop], [log]; Notes: long-running dispatcher process; Ctrl+C to exit


# core/watchdog.py.md

$PROJECT_ROOT/core/watchdog.py — Library
core.watchdog.is_game_foregrounded() — Returns: True iff GAME_PACKAGE is currently foregrounded; logs foreground changes. Side effects: [adb][log]
core.watchdog.bring_to_foreground() — Returns: action result (monkey launch intent sent, 5s wait). Side effects: [adb][log]
core.watchdog.restart_game() — Returns: action result (force-stop then monkey relaunch; sets AUTOMATION.state=UNKNOWN). Side effects: [adb][state][log]
core.watchdog.watchdog_process_check(interval=30) — Returns: [loop] supervisory check; restarts or foregrounds app as needed. Side effects: [adb][state][log]; Errors: KeyboardInterrupt stops loop; other exceptions logged and loop continues


# handlers/ad_gem_handler.py.md

$PROJECT_ROOT/handlers/ad_gem_handler.py — Library
handlers.ad_gem_handler.start_blind_gem_tapper(duration=20, interval=1, blocking=False) — Returns: None; Side effects: [thread][tap][log]; Notes: non-reentrant via _blind_tapper_active; validates duration/interval; resolves click 'gesture_targets.floating_gem_blind_tap' before starting; blocking runs in caller thread; non-blocking spawns non-daemon thread with cooperative cancel; Errors: None raised (invalid inputs or missing coords are logged and function returns).
handlers.ad_gem_handler.stop_blind_gem_tapper() — Returns: True if a running tapper was signaled to stop; False otherwise; Side effects: [signal][log]; Notes: sets internal stop Event checked by worker for fast shutdown; Errors: None.
handlers.ad_gem_handler.handle_ad_gem() — Returns: None; Side effects: [tap][thread][log][sleep]; Workflow: ensures background blind tapper (20s @ 1s) is running, taps 'overlays.ad_gem', waits 1s; Errors: None raised (failures logged).


# handlers/game_over_handler.py.md

$PROJECT_ROOT/handlers/game_over_handler.py — Library
handlers.game_over_handler.handle_game_over() — Returns: action result (captures stats pages, closes stats, then retries or pauses per ExecMode); Side effects: [adb][cv2][fs][tap][swipe][log][loop]; Defaults: several sleeps ≈1.2–1.5s between actions plus final 2s; Errors: aborts via _abort_handler() on tap failures.
handlers.game_over_handler._make_session_id() — Returns: session ID string "GameYYYYMMDD_%H%M"; Side effects: none.
handlers.game_over_handler.save_image(img, tag) — Returns: None; Side effects: [cv2][fs][log]; Errors: skips write when img is None.
handlers.game_over_handler._abort_handler(step, session_id) — Returns: None; Side effects: [adb][cv2][fs][log]; Sets AUTOMATION.mode=WAIT; Errors: none (terminates handler flow).


# handlers/home_screen_handler.py.md

$PROJECT_ROOT/handlers/home_screen_handler.py — Library
handlers.home_screen_handler.handle_home_screen(restart_enabled=True) — Returns: action result (side effects only); Side effects: [tap][log]


# handlers/mission_demon_mode.py.md

$PROJECT_ROOT/handlers/mission_demon_mode.py — Entrypoint
handlers.mission_demon_mode.MissionOutcome — Enum: outcomes for a single round (SUCCESS, TIMEOUT_WAITING_FOR_RUNNING, TIMEOUT_WAITING_FOR_DEMON, UI_FLOW_FAILURE, ABORTED_BY_USER).
handlers.mission_demon_mode.MissionResult — Class: result for one round (outcome, details, elapsed_s, per-phase timings, errors).
handlers.mission_demon_mode.MissionConfig — Class: timeouts/intervals (RUNNING 60s, DEMON 45s; overall 240s), wait (post-Demon 75s legacy default), verify_tap with up to 2 retries.
handlers.mission_demon_mode.CampaignResult — Class: aggregates for multi-round campaign (runs, successes, timeouts_running, timeouts_demon, ui_failures, aborted, total_elapsed_s, last_result, progress).
handlers.mission_demon_mode.run_demon_mode_strategy(config=None, *, dry_run=False, on_event=None) — Returns: MissionResult (outcome enum + timings + errors); Side effects: [adb][cv2][fs][state][tap][log]; Defaults: bounded waits (RUNNING 60s, Demon 45s), overall 240s cap, post-Demon 75s wait, verify tap with up to 2 retries; Errors: only programmer errors raise; user interrupt returns ABORTED_BY_USER.
handlers.mission_demon_mode.run_demon_mode_campaign(config=None, *, max_runs=None, max_duration_s=None, sleep_between_runs_s=2.0, stopfile=None, progress_detector=None, until=None, on_event=None, dry_run=False) — Returns: CampaignResult (aggregated outcomes + last_result + optional progress); Side effects: [adb][cv2][fs][state][tap][log][loop]; Defaults: repeats rounds until a bound/stop condition is met; Errors: only programmer errors raise; user interrupt sets aborted=True.
handlers.mission_demon_mode.run_demon_mode(wait_seconds=75) — Returns: action result (back-compat wrapper; delegates to run_demon_mode_strategy and discards result); Side effects: [adb][cv2][fs][state][tap][log]; Defaults: post-Demon wait 75s.


# handlers/mission_demon_nuke.py.md

$PROJECT_ROOT/handlers/mission_demon_nuke.py — Entrypoint
handlers.mission_demon_nuke.MissionOutcome — Enum: outcomes for a single round (SUCCESS, TIMEOUT_* , UI_FLOW_FAILURE, ABORTED_BY_USER).
handlers.mission_demon_nuke.MissionResult — Class: result for one round (outcome, details, elapsed_s, per-phase timings, errors).
handlers.mission_demon_nuke.MissionConfig — Class: timeouts/intervals (RUNNING 60s, DEMON 45s, NUKE 45s; overall 240s), waits (10s post-Demon, 5s post-Nuke), verify_tap with up to 2 retries.
handlers.mission_demon_nuke.CampaignResult — Class: aggregates for multi-round campaign (runs, successes, timeouts, ui_failures, aborted, total_elapsed_s, last_result, progress).
handlers.mission_demon_nuke.run_demon_nuke_strategy(config=None, *, dry_run=False, on_event=None) — Returns: MissionResult (outcome enum + timings + errors); Side effects: [adb][cv2][fs][state][tap][log]; Defaults: bounded waits (RUNNING 60s, Demon 45s, Nuke 45s), overall 240s cap, 10s after Demon, 5s after Nuke, verify tap with up to 2 retries; Errors: only programmer errors raise; user interrupt returns ABORTED_BY_USER.
handlers.mission_demon_nuke.run_demon_nuke_campaign(config=None, *, max_runs=None, max_duration_s=None, sleep_between_runs_s=2.0, stopfile=None, progress_detector=None, until=None, on_event=None, dry_run=False) — Returns: CampaignResult (aggregated outcomes + last_result + optional progress); Side effects: [adb][cv2][fs][state][tap][log][loop]; Defaults: repeats rounds until a bound/stop condition is met; Errors: only programmer errors raise; user interrupt sets aborted=True.


# test/detect_floating_buttons.py.md

$PROJECT_ROOT/test/detect_floating_buttons.py — Entrypoint
test.detect_floating_buttons.main() — Returns: action result (captures screen, runs detect_floating_buttons, prints matches); Side effects: [adb][state]; Errors: None (returns early when screencap fails).


# test/detect_state_test.py.md

$PROJECT_ROOT/test/detect_state_test.py — Entrypoint
test.detect_state_test.main() — Returns: action result (prints detected state/overlays; optional annotated image write); Side effects: [adb][cv2][fs][state]; Errors: exits early if image path missing or load fails; CLI: --image PATH (default screenshots/latest.png), --highlight, --refresh.


# test/test_game_over_handler.py.md

$PROJECT_ROOT/test/test_game_over_handler.py — Entrypoint
test.test_game_over_handler.run_test() — Returns: action result (logs lifecycle; restores automation mode); Side effects: [state][log][adb][cv2][fs][tap][swipe]; Errors: exceptions from handler are caught/logged with traceback; automation mode always restored.


# test/test_gesture.py.md

$PROJECT_ROOT/test/test_gesture.py — Entrypoint
test.test_gesture.run_gesture(dot_path) — Returns: True if a gesture executes (visual tap success for match_template; otherwise static tap/swipe executed); Side effects: [cv2][tap][swipe][adb][log]; Errors: resolve failure logged and returns False; ADB/tap errors may surface via called utilities.
test.test_gesture.main() — Returns: action result (runs one gesture); Side effects: [cv2][tap][swipe][adb][log]; Errors: process exits with code 1 on failure; CLI: dot_path.


# test/test_upgrade_detection.py.md

$PROJECT_ROOT/test/test_upgrade_detection.py — Entrypoint
test.test_upgrade_detection.classify_color(bgr) — Returns: one of {"maxed","upgradeable","unaffordable"} based on average(B,G,R) thresholds (MAXED_RANGE, UPGRADEABLE_RANGE); Side effects: none; Errors: none.
test.test_upgrade_detection.detect_upgrades(screen, keys) — Returns: dict mapping each key→{status, confidence[, tap_point, avg_color]} where status ∈ {"maxed","upgradeable","unaffordable","not visible","clickmap entry missing","sample_oob"}; Side effects: [cv2] draws a small green rectangle on the sampled color location; Errors: none (out-of-bounds sampling reported as status="sample_oob" instead of raising).
test.test_upgrade_detection.main([image_path]) — Returns: action result (UI preview + printed results); Side effects: [fs][cv2]; Errors: returns 1 if image load fails. CLI: optional image path overrides screenshots/latest.png.


# tools/crop_region.py.md

$PROJECT_ROOT/tools/crop_region.py — Entrypoint
tools.crop_region.reload_image() — Returns: refreshed screenshot in globals (image/clone), resets scroll_offset; Side effects: [adb][cv2][fs]; Errors: Raises RuntimeError when screenshot capture fails.
tools.crop_region.is_coords_only(dot_path) — Returns: True if dot_path is in coords-only groups/prefixes; Side effects: none.
tools.crop_region.save_template_crop_and_entry(x1, y1, x2, y2) — Returns: action result (saves template/region to disk and clickmap; optional gesture logging); Side effects: [cv2][fs][adb][log]; Defaults: prompts for threshold (default 0.90) and roles; Errors: Returns early on invalid input; downstream failures surfaced by called functions.
tools.crop_region.handle_mouse(event, x, y, flags, param) — Returns: action result (updates selection/scroll; may persist region via save_template_crop_and_entry); Side effects: [cv2]; Errors: none material.


# tools/gesture_logger.py.md

$PROJECT_ROOT/tools/gesture_logger.py — Entrypoint
tools.gesture_logger.ScrcpyBridge — Class: manages the scrcpy worker process that streams JSON gestures from scrcpy_adb_input_bridge.py.
tools.gesture_logger.ScrcpyBridge.start() — Returns: None; Side effects: [log]; Errors: OSError if process spawn fails.
tools.gesture_logger.ScrcpyBridge.ensure_running() — Returns: None; Side effects: [log]; Errors: same as start() if restart needed and spawn fails.
tools.gesture_logger.ScrcpyBridge.stop() — Returns: None; Side effects: [log]; Errors: None (kills after timeout if needed).
tools.gesture_logger.ScrcpyBridge.__enter__() — Returns: self (bridge ready after a short settle); Side effects: [log]; Errors: propagate from ensure_running().
tools.gesture_logger.ScrcpyBridge.__exit__(exc_type, exc, tb) — Returns: None; Side effects: [log]; Errors: None (best-effort stop).
tools.gesture_logger.ScrcpyBridge.flush_old() — Returns: None (discards buffered JSON gesture lines); Side effects: [log]; Errors: None (no-op if stdout unavailable).
tools.gesture_logger.ScrcpyBridge.read_gesture() — Returns: dict describing one gesture (e.g., {"type":"tap","x":...} or {"type":"swipe","x1":...,"y1":...,"x2":...,"y2":...,"duration_ms":...}); Side effects: [log]; Errors: RuntimeError if bridge not running/stdout unavailable or if process exits before a gesture; JSON decode errors are logged and skipped.
tools.gesture_logger.replay_gesture(gesture) — Returns: action result (injects the gesture on device); Side effects: [adb][log]; Errors: CalledProcessError when ADB command fails (via adb_shell).
tools.gesture_logger.ensure_entry(dot_path) — Returns: (clickmap_dict, entry_dict) if created or found; (None, None) if user declines; Side effects: [fs][log]; Errors: None (interactive prompt).
tools.gesture_logger.record_and_save(bridge, dot_path) — Returns: None; Side effects: [fs][adb][log]; Errors: Propagates RuntimeError from read_gesture(); unsupported gesture types are logged and skipped.
tools.gesture_logger.main() — Returns: action result (interactive loop unless --name is provided); Side effects: [loop][fs][adb][log]; Errors: KeyboardInterrupt cleanly exits; RuntimeError from read_gesture() propagates if not in the Ctrl+C path; CLI: --name <dot_path> saves exactly one gesture then exits.


# tools/run_blind_gem_tapper.py.md

$PROJECT_ROOT/tools/run_blind_gem_tapper.py — Entrypoint
tools.run_blind_gem_tapper.start_blind_gem_tapper(duration=seconds, interval=seconds, blocking=False) — Returns: starts the blind floating gem tapper (foreground when blocking=True, otherwise daemon thread); Side effects: [tap][log][loop]; Errors: None material (inputs ≤0 are logged and aborted; non-reentrant—silently no-op if already active)
tools.run_blind_gem_tapper.main() — Entrypoint: CLI flags --duration, --interval, --blocking


# tools/scrcpy_adb_input_bridge.py.md

$PROJECT_ROOT/tools/scrcpy_adb_input_bridge.py — Entrypoint
tools.scrcpy_adb_input_bridge.ensure_scrcpy_window_rect(rect_source='top', diagnose=False, android_size=None) — Returns: (x, y, w, h) chosen from top/child/auto; Side effects: [log]; Errors: RuntimeError if the window cannot be found.
tools.scrcpy_adb_input_bridge.get_android_screen_size() — Returns: (width, height) from capture_adb_screenshot(); Side effects: [adb][cv2]; Errors: RuntimeError if capture fails.
tools.scrcpy_adb_input_bridge.get_scrcpy_window_rect(rect_source='top', diagnose=False, android_size=None) — Returns: (x, y, w, h) using the current selection policy; Side effects: [log]; Errors: RuntimeError if window cannot be found.
tools.scrcpy_adb_input_bridge.map_to_android(x, y, window_rect, android_size) — Returns: (ax, ay) mapped Android coordinates with letterboxing handled; Side effects: None; Errors: None.
tools.scrcpy_adb_input_bridge.send_tap(x, y) — Returns: action result (inject tap); Side effects: [adb][log]; Errors: CalledProcessError when ADB command fails (via adb_shell).
tools.scrcpy_adb_input_bridge.send_swipe(x1, y1, x2, y2, duration_ms) — Returns: action result (inject swipe); Side effects: [adb][log]; Errors: CalledProcessError when ADB command fails (via adb_shell).
tools.scrcpy_adb_input_bridge.get_pixel_color_at_android_coords(x, y) — Returns: (R, G, B) at Android coords or None on failure; Side effects: [adb][cv2][log]; Errors: Exceptions caught and logged; returns None.
tools.scrcpy_adb_input_bridge.start_mouse_listener(android_size, args) — Returns: None (starts background listener thread); Side effects: [loop][adb][log]; Errors: Non-fatal logging on window lookup errors; emits JSON lines when --json-stream is set.
tools.scrcpy_adb_input_bridge.launch_scrcpy() — Returns: None; Side effects: starts scrcpy subprocess titled "scrcpy-bridge"; [log]; Errors: OSError if spawn fails.
tools.scrcpy_adb_input_bridge.cleanup_and_exit(signum=None, frame=None) — Returns: None; Side effects: [log]; Errors: None (best-effort terminate/kill of scrcpy).
tools.scrcpy_adb_input_bridge.main() — Returns: action result (runs bridge until killed); Side effects: [loop][adb][log]; Errors: Process exits on SIGINT/SIGTERM; CLI: --json-stream emits "__GESTURE_JSON__{...}" lines for gestures; --rect-source {top,child,auto} selects rect policy (default top); --rect-diagnose prints candidate/AR info.


# tools/tune_gesture.py.md

$PROJECT_ROOT/tools/tune_gesture.py — Entrypoint
tools.tune_gesture.load_clickmap() — Returns: in-memory clickmap dict; Side effects: None; Errors: None (delegates to get_clickmap()).
tools.tune_gesture.run_adb_swipe(x1, y1, x2, y2, duration) — Returns: action result (inject swipe); Side effects: [adb][log]; Errors: CalledProcessError when ADB command fails (via adb_shell).
tools.tune_gesture.choose_gesture(clickmap) — Returns: (name, entry_dict) selected interactively; Side effects: [log][loop]; Errors: ValueError reprompt on invalid input.
tools.tune_gesture.edit_swipe(name, swipe_entry) — Returns: updated swipe dict on save, None on back; Side effects: [adb][log][loop]; Errors: None (no bounds checking on coordinates).
tools.tune_gesture.run_tap(name) — Returns: None; Side effects: [tap][log][loop]; Errors: None (calls tap_now(name) on 'r').
tools.tune_gesture.print_controls() — Returns: None; Side effects: [log]; Errors: None.
tools.tune_gesture.main() — Returns: action result (interactive tuner loop); Side effects: [loop][fs][adb][tap][log]; Errors: Process exits on 'q'; relies on clickmap entries containing 'tap' or 'swipe'.


# utils/logger.py.md

$PROJECT_ROOT/utils/logger.py — Library
utils.logger.log(msg, level="INFO") — Returns: None (writes formatted log entry to stdout and logs/actions.log); Side effects: [fs][log]; Defaults: log level defaults to "INFO"; Ensures logs/ directory exists before writing; Errors: OSError if unable to create directory or write file.


# utils/ocr_utils.py.md

$PROJECT_ROOT/utils/ocr_utils.py — Library
utils.ocr_utils.preprocess_binary(img_bgr, *, alpha=1.6, block=31, C=5, close=(2,2), invert=False, choose_best=False) — Returns: single-channel 0/255 image tuned for OCR (contrast boost → adaptive threshold → optional invert/best-pick → morphological close); Side effects: [cv2]; Defaults: choose_best picks normal vs inverted by black-pixel count; Errors: none material.
utils.ocr_utils.ocr_text(bin_img, *, psm=6) — Returns: OCR’d text as str ("" if Tesseract unavailable); Side effects: [cv2]; Errors: none material.
utils.ocr_utils.ocr_digits(bin_img, *, psm=7, whitelist="0123456789") — Returns: (value:int|None, avg_conf:float|-1.0, raw_text:str) using digit-only OCR; Side effects: [cv2]; Defaults: digit whitelist; Errors: none material.


# utils/previous_wave.py.md

$PROJECT_ROOT/utils/previous_wave.py — Library|Entrypoint
utils.previous_wave.get_previous_run_wave(matches_dir="screenshots/matches") — Returns: previous run’s current wave number as int|None by loading the latest GameYYYYMMDD_HHMM_game_stats.png, binarizing via utils.ocr_utils.preprocess_binary, OCRing with utils.ocr_utils.ocr_text, and parsing "Wave N"; Side effects: [cv2][fs]; Defaults: scans screenshots/matches; Errors: None explicit; returns None if no file, image load fails, or OCR/parse fails.
utils.previous_wave.main() — Returns: action result (CLI output only); Side effects: [cv2][fs]; CLI flags: --matches-dir; Errors: same as get_previous_run_wave; exits after printing result.


# utils/wave_detector.py.md

$PROJECT_ROOT/utils/wave_detector.py — Library|Entrypoint
utils.wave_detector.detect_wave_number_from_image(img_bgr, dot_path="_shared_match_regions.wave_number", debug_out=None) — Returns: (wave_number:int|None, confidence:float[-1..100]) after OCR of the configured on-screen region; Side effects: [cv2][fs]; Defaults: writes preprocessed crop to debug_out if provided; Errors: KeyError when clickmap region missing/malformed; ValueError when crop bbox invalid.
utils.wave_detector.detect_wave_number(dot_path="_shared_match_regions.wave_number", debug_out=None) — Returns: (wave_number:int|None, confidence:float[-1..100]) by capturing a fresh ADB screenshot then delegating to detect_wave_number_from_image; Side effects: [adb][cv2][fs]; Errors: RuntimeError when screenshot capture fails; KeyError/ValueError propagate from region lookup/cropping.
utils.wave_detector.get_wave_number(dot_path="_shared_match_regions.wave_number") — Returns: wave_number:int|None for convenience workflows; Side effects: [adb][cv2]; Errors: RuntimeError/KeyError/ValueError propagate from detect_wave_number.
utils.wave_detector.main() — Returns: action result (CLI output only); Side effects: [adb][cv2][fs]; CLI flags: --dot-path, --debug-out; Errors: same as detect_wave_number; exits after printing result.


