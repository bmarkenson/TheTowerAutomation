import cv2
import os

TEMPLATES = {
    "resume_game": "assets/match_templates/resume_game.png",
    "resume_battle": "assets/match_templates/resume_battle.png"
}

MATCH_THRESHOLD = 0.90  # Tune if needed

template_images = {
    name: cv2.imread(path, cv2.IMREAD_COLOR)
    for name, path in TEMPLATES.items()
}

template_sizes = {
    name: img.shape[1::-1]
    for name, img in template_images.items()
}

def detect_resume_button(screen):
    """Returns the name of the resume screen matched, or None"""
    for name, template in template_images.items():
        res = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(res)
        if max_val >= MATCH_THRESHOLD:
            return name, max_val
    return None, None
