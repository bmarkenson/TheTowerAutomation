$PROJECT_ROOT/tools/tune_gesture.py — Entrypoint
tools.tune_gesture.load_clickmap() — Returns: in-memory clickmap dict; Side effects: None; Errors: None (delegates to get_clickmap()).
tools.tune_gesture.run_adb_swipe(x1, y1, x2, y2, duration) — Returns: action result (inject swipe); Side effects: [adb][log]; Errors: CalledProcessError when ADB command fails (via adb_shell).
tools.tune_gesture.choose_gesture(clickmap) — Returns: (name, entry_dict) selected interactively; Side effects: [log][loop]; Errors: ValueError reprompt on invalid input.
tools.tune_gesture.edit_swipe(name, swipe_entry) — Returns: updated swipe dict on save, None on back; Side effects: [adb][log][loop]; Errors: None (no bounds checking on coordinates).
tools.tune_gesture.run_tap(name) — Returns: None; Side effects: [tap][log][loop]; Errors: None (calls tap_now(name) on 'r').
tools.tune_gesture.print_controls() — Returns: None; Side effects: [log]; Errors: None.
tools.tune_gesture.main() — Returns: action result (interactive tuner loop); Side effects: [loop][fs][adb][tap][log]; Errors: Process exits on 'q'; relies on clickmap entries containing 'tap' or 'swipe'.
