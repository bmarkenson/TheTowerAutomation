handlers/game_over_handler.py
handlers.game_over_handler.handle_game_over() — R: action result (captures stats pages, closes stats, then retries or pauses per ExecMode); S: [adb][cv2][fs][tap][swipe][log][loop]; Defaults: several sleeps ≈1.2–1.5s between actions plus final 2s; E: aborts via _abort_handler() on tap failures.
handlers.game_over_handler._make_session_id() — R: session ID string "GameYYYYMMDD_%H%M"; S: none.
handlers.game_over_handler.save_image(img, tag) — R: None; S: [cv2][fs][log]; E: skips write when img is None.
handlers.game_over_handler._abort_handler(step, session_id) — R: None; S: [adb][cv2][fs][log]; Sets AUTOMATION.mode=WAIT; E: none (terminates handler flow).
