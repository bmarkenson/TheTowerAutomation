tools/tune_gesture.py
tools.tune_gesture.load_clickmap() — R: in-memory clickmap dict; S: None; E: None (delegates to get_clickmap()).
tools.tune_gesture.run_adb_swipe(x1, y1, x2, y2, duration) — R: action result (inject swipe); S: [adb][log]; E: CalledProcessError when ADB command fails (via adb_shell).
tools.tune_gesture.choose_gesture(clickmap) — R: (name, entry_dict) selected interactively; S: [log][loop]; E: ValueError reprompt on invalid input.
tools.tune_gesture.edit_swipe(name, swipe_entry) — R: updated swipe dict on save, None on back; S: [adb][log][loop]; E: None (no bounds checking on coordinates).
tools.tune_gesture.run_tap(name) — R: None; S: [tap][log][loop]; E: None (calls tap_now(name) on 'r').
tools.tune_gesture.print_controls() — R: None; S: [log]; E: None.
tools.tune_gesture.main() — R: action result (interactive tuner loop); S: [loop][fs][adb][tap][log]; E: Process exits on 'q'; relies on clickmap entries containing 'tap' or 'swipe'.
