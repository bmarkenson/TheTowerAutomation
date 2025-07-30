# core/state_detector.py
from matchers.game_over import match_game_over
from utils.logger import log

def detect_state(screen):
    pt, confidence = match_game_over(screen)
    if pt:
        log(f"[MATCH] GAME OVER matched at {pt} with confidence {confidence:.3f}", "MATCH")
        return "GAME_OVER"

    # Future: Add more matchers here (e.g., upgrade screen, idle state, ads, etc.)
    return "RUNNING"


