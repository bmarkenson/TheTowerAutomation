$PROJECT_ROOT/handlers/game_over_handler.py — Library
handlers.game_over_handler.handle_game_over() — Returns: action result (captures stats pages, closes stats, then retries or pauses per ExecMode); Side effects: [adb][cv2][fs][tap][swipe][log][loop]; Defaults: several sleeps ≈1.2–1.5s between actions plus final 2s; Errors: aborts via _abort_handler() on tap failures.
handlers.game_over_handler._make_session_id() — Returns: session ID string "GameYYYYMMDD_%H%M"; Side effects: none.
handlers.game_over_handler.save_image(img, tag) — Returns: None; Side effects: [cv2][fs][log]; Errors: skips write when img is None.
handlers.game_over_handler._abort_handler(step, session_id) — Returns: None; Side effects: [adb][cv2][fs][log]; Sets AUTOMATION.mode=WAIT; Errors: none (terminates handler flow).
