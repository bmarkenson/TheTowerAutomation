PROJECT PRIMER — goals & constraints for all answers in this thread

GAME/TARGET
- Android game: “The Tower: Idle Tower Defense” on BlueStacks (Win11) with adb; Linux host runs Python+OpenCV+scrcpy.

CORE GOALS (ranked)
1) 24/7 unattended stability (survive ADB drops, UI stalls, crashes; recover automatically).
2) Visual state awareness (analyze screen before acting; no blind macros).
3) Reliable input injection with correct path:
   - tap_dispatcher for queued/periodic/low-prio taps
   - tap_now/swipe_now for immediate feedback-gated actions
   - tap_label_now for label-relative taps
4) Manual override safe: pause/resume; don’t fight scrcpy/manual input.
5) Minimal stack + modularity (clean separation: core/ handlers/ config/ assets/ tools/; reusable beyond this game).

ARCHITECTURE SNAPSHOT
- Detection: clickmap.json (static visual spec) + state_definitions.yaml (logic/semantics). Matching via OpenCV TM_CCOEFF_NORMED.
- Control flow: state_detector -> (primary state + overlays) -> handler dispatch.
- Handlers: function-based now; migrating to class-based with register/should_run/run. Watchdog ensures foreground/relaunch.
- Input: dual-path taps (see above). Logging to stdout + logs/actions.log.

NON-NEGOTIABLE RULES
- Never propose blind input; always justify via visible evidence or overlay/state match.
- Respect watchdog and pause/resume semantics; don’t bypass dispatcher without cause.
- Keep device targeting explicit if ambiguity matters; otherwise project default.

IMPLEMENTED TODAY (high level)
- States: GAME_OVER, HOME_SCREEN, RUNNING, RESUME_GAME (+ early overlays: floating gem/ad_gem).
- Handlers: game_over, home_screen, ad_gem; mission_* (demon_mode, demon_nuke) strategies exist.
- Tools: crop_region, gesture_logger, tune_gesture. Watchdog present. OCR utilities available.

DEFAULTS & INVARIANTS (only those that affect decisions)
- Threshold default = 0.90 (override per case if justified).
- Images are BGR; coords origin top-left; regions {x,y,w,h}. Paths: assets/match_templates/, screenshots/.
- config/clickmap.json = static visuals only; config/state_definitions.yaml = behavior/logic. Don’t mix them.

DESIGN HEURISTICS I WILL APPLY
- Prefer deterministic checks (template score, color/box state, OCR digits) before taps.
- Prefer idempotent handlers; avoid race conditions with tap queue.
- If unsure: propose a minimal experiment (single image test or dry-run matcher) before code changes.

WHAT I NEED FROM YOU WHEN ASKING FOR CODE/CHANGES
- Paste only the files to edit + any directly referenced schemas (clickmap/state_definitions) if relevant.
- If priorities shift, give a 1-line “Delta:” note (e.g., “Delta: per-state thresholds; add Daily Quests overlay.”)


Project function map — compact spec used for all answers in this thread.
Always prefer to use existing functions rather than re-implementing, if possible.

# Tags
[adb]=runs device cmds; may block; raises CalledProcessError when check=True. [cv2]=BGR ndarrays; TM_CCOEFF_NORMED. [fs]=disk I/O. [state]=UI/game state eval. [tap]=inject tap. [swipe]=inject swipe. [log]=stdout+logs/actions.log. [loop]=repeats. [thread]=bg thread. [signal]=events/flags. [sleep]=time.sleep.

# Non-negotiable rules
- Never propose blind macros. All input is visual-state-aware.
- Use tap paths correctly: tap_dispatcher for queued/periodic; tap_now/swipe_now for immediate feedback-gated; tap_label_now for label-relative taps.
- Respect watchdog, pause/resume, and handler conditions.

# Modules (R=return, S=side effects, E=errors, CLI flags)
automation/run_demon_mode.py
- main(delay=2, once=False) — R None; S [loop][log][adb]; E KeyboardInterrupt stops loop; others logged+retry.

automation/run_demon_nuke.py
- main() — R None; S [loop][log]; E KeyboardInterrupt; others logged+retry after 2s.

core/adb_utils.py
- adb_shell(cmd, capture_output=False, check=True, device_id=None) — R CompletedProcess|None; S [adb]; E None on CalledProcessError/Exception (stderr printed).
- screencap_png(device_id=None, check=True) — R bytes|None; S [adb]; E None on failure/invalid.

core/automation_state.py
- AutomationControl.state/mode properties — typed setters; E ValueError/TypeError.
- Enums: RunState={RUNNING,PAUSED,STOPPED,UNKNOWN}; ExecMode={RETRY,WAIT,HOME}.

core/clickmap_access.py
- get_clickmap()/get_clickmap_path()/save_clickmap() — R dict|str|None; S [fs].
- resolve_dot_path()/dot_path_exists()/set_dot_path(..., allow_overwrite=False) — R Any|bool|None; E KeyError/ValueError on bad paths.
- get_click()/get_swipe()/has_click() — R (x,y)|dict|bool.
- tap_now(name)/swipe_now(name) — R None; S [adb][log]; E CalledProcessError.
- flatten_clickmap(prefix="")/get_entries_by_role(role) — R dict.

core/floating_button_detector.py
- detect_floating_buttons(screen) — R list[dict]; S [cv2][state][log]; partial on exceptions.
- tap_floating_button(name, buttons) — R bool; S [adb][log].

core/label_tapper.py
- resolve_region(entry, clickmap) — R dict; E ValueError on bad region_ref.
- get_label_match(label_key, screenshot=None, return_meta=False) — R bbox|meta; S [adb].
- tap_label_now(label_key) — R bool; S [tap][adb][log].
- is_visible(label_key, screenshot=None) — R bool; S [adb].

core/ss_capture.py
- capture_adb_screenshot()/capture_and_save_screenshot(path) — R ndarray|None; S [adb][cv2][fs][log].

core/state_detector.py
- load_state_definitions() — R dict; S [fs]; E FileNotFoundError/YAMLError.
- detect_state_and_overlays(screen) — R dict; S [cv2][state][log]; E RuntimeError on multiple primary states.

core/tap_dispatcher.py
- log_tap(x,y,label)/tap(x,y,label=None)/main() — R None; S [tap][log][loop].

core/watchdog.py
- is_game_foregrounded() — R bool; S [adb][log].
- bring_to_foreground()/restart_game()/watchdog_process_check(interval=30) — R None; S [adb][state][log][loop]; E KeyboardInterrupt stops loop.

handlers/ad_gem_handler.py
- start_blind_gem_tapper(duration=20, interval=1, blocking=False) — R None; S [thread][tap][log].
- stop_blind_gem_tapper() — R bool; S [signal][log].
- handle_ad_gem() — R None; S [tap][thread][log][sleep].

handlers/game_over_handler.py
- handle_game_over() — R None; S [adb][cv2][fs][tap][swipe][log][loop].
- _make_session_id() — R str.  save_image(img, tag) — R None; S [cv2][fs][log].

handlers/home_screen_handler.py
- handle_home_screen(restart_enabled=True) — R None; S [tap][log].

handlers/mission_demon_mode.py / mission_demon_nuke.py
- *Mission* Enums/Result/Config/CampaignResult defined.
- run_*_strategy(...) — R MissionResult; S [adb][cv2][fs][state][tap][log].
- run_*_campaign(...) — R CampaignResult; S [adb][cv2][fs][state][tap][log][loop].
- run_demon_mode(wait_seconds=75) — R None; S [adb][cv2][fs][state][tap][log].

test/*
- detect_floating_buttons.main(), detect_state_test.main(--image/--highlight/--refresh),
  test_game_over_handler.run_test(), test_gesture.run_gesture(dot_path)/main(), test_upgrade_detection.main()
  — S varies: [adb][cv2][fs][state][tap][swipe][log].

tools/*
- crop_region.reload_image()/save_template_crop_and_entry()/handle_mouse(...) — S [adb][cv2][fs][log].
- gesture_logger.ScrcpyBridge(start/ensure_running/stop/ctxmgr/flush_old/read_gesture) + record_and_save/ensure_entry/replay_gesture/main(--name).
- run_blind_gem_tapper.main(--duration --interval --blocking); start_blind_gem_tapper(...).
- scrcpy_adb_input_bridge.ensure_scrcpy_window_rect/get_android_screen_size/get_scrcpy_window_rect/map_to_android/send_tap/send_swipe/get_pixel_color_at_android_coords/start_mouse_listener/launch_scrcpy/cleanup_and_exit/main(--json-stream --rect-source --rect-diagnose).
- tune_gesture.load_clickmap/run_adb_swipe/choose_gesture/edit_swipe/run_tap/print_controls/main().

utils/*
- logger.log(msg, level="INFO") — S [fs][log].
- ocr_utils.preprocess_binary/ocr_text/ocr_digits — S [cv2].
- previous_wave.get_previous_run_wave(matches_dir="screenshots/matches") — R int|None; S [cv2][fs].
- wave_detector.detect_wave_number_from_image(img, dot_path="_shared_match_regions.wave_number", debug_out=None) — R tuple|None; S [cv2][fs].
  detect_wave_number(...), get_wave_number(...), main(--dot-path --debug-out) — may S [adb][cv2][fs].

---

### CANONICAL DATA FORMATS (ground truth — minimal but authoritative)

**`config/clickmap.json` — shape & tiny examples**
```json
{
  "_shared_match_regions": {
    "wave_number": { "match_region": { "x": 562, "y": 1034, "w": 309, "h": 52 } },
    "floating_buttons": { "match_region": { "x": 18, "y": 796, "w": 655, "h": 106 } },
    "ad_gem_region": { "match_region": { "x": 11, "y": 616, "w": 272, "h": 286 } }
  },

  "indicators": {
    "menu_attack": {
      "match_template": "indicators/menu_attack.png",
      "match_region": { "x": 16, "y": 1169, "w": 215, "h": 65 },
      "match_threshold": 0.9
    },
    "top:more_stats": {
      "match_template": "indicators/top:more_stats.png",
      "match_region": { "x": 415, "y": 359, "w": 240, "h": 45 }
    }
  },

  "overlays": {
    "ad_gem": {
      "match_template": "overlays/ad_gem.png",
      "region_ref": "ad_gem_region",
      "match_threshold": 0.9
    }
  },

  "buttons": {
    "resume_battle:home": {
      "match_template": "buttons/resume_battle:home.png",
      "match_region": { "x": 291, "y": 1470, "w": 499, "h": 177 }
    }
  },

  "gesture_targets": {
    "goto_top:more_stats": {
      "swipe": { "type": "swipe", "x1": 490, "y1": 420, "x2": 511, "y2": 1457, "duration_ms": 230 }
    },
    "floating_gem_blind_tap": {
      "tap": { "type": "tap", "x": 542, "y": 671 }
    }
  },

  "upgrades": {
    "attack": {
      "left": {
        "damage": {
          "match_template": "upgrades/attack/left/damage.png",
          "match_region": { "x": 40, "y": 1260, "w": 480, "h": 120 }
        }
      },
      "right": {}
    }
  }
}
```

### Authoritative parsing rules

- Dot-paths (e.g., `indicators.menu_attack`) map to nested dict keys exactly as spelled.
- **Colons are literal** parts of keys (e.g., `"retry:game_over"`). Do **not** split on `:`.
- If both `match_region` and `region_ref` exist, **`match_region` wins**; otherwise `region_ref` resolves to `_shared_match_regions.<key>`.
- Missing `match_threshold` ⇒ use global default (0.90).
- `roles` may appear but are advisory only (no logic inferred).
- Only `gesture_targets` stores absolute tap/swipe coordinates. Everywhere else, taps are **label-relative** to the matched bbox center.

### Tap semantics

- `tap_label_now(key)`: tap the **center of the matched bbox** for `key`. No offsets in `clickmap.json`; any offsets live in code/YAML.
- Path discipline: `tap_dispatcher` = queued/periodic; `tap_now`/`swipe_now` = immediate feedback-gated; `tap_label_now` = label-relative.

### `config/state_definitions.yaml` — shape & tiny examples

```uaml
states:
  - name: RUNNING
    type: primary
    match_keys:
      - indicators.tournament
      - indicators.menu_attack
      - indicators.menu_defense
      - indicators.menu_utility
      - indicators.menu_uw
      - indicators.cinematic_wall_icon

  - name: HOME_SCREEN
    type: primary
    match_keys:
      - indicators.home_screen

  - name: GAME_OVER
    type: primary
    match_keys:
      - indicators.game_over

overlays:
  - name: DAILY_GEMS_AVAILABLE
    match_keys: [overlays.daily_free_gems_badge]

  - name: AD_GEMS_AVAILABLE
    match_keys: [overlays.ad_gem]
```

### Detector guarantees

- Exactly **one** primary state per frame; multiple primaries ⇒ raise.  0 primaries -> UNKNOWN
- Overlays: zero to many may co-exist.


