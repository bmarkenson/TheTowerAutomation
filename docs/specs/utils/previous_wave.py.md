
utils/previous_wave.py
utils.previous_wave.get_previous_run_wave(matches_dir="screenshots/matches") — R: previous run’s current wave number as int|None by loading the latest GameYYYYMMDD_HHMM_game_stats.png, binarizing via utils.ocr_utils.preprocess_binary, OCRing with utils.ocr_utils.ocr_text, and parsing "Wave N"; S: [cv2][fs]; Defaults: scans screenshots/matches; E: None explicit; returns None if no file, image load fails, or OCR/parse fails.
utils.previous_wave.main() — R: action result (CLI output only); S: [cv2][fs]; CLI flags: --matches-dir; E: same as get_previous_run_wave; exits after printing result.
