$PROJECT_ROOT/core/state_detector.py — Library
core.state_detector.load_state_definitions() — Returns: dict parsed from config/state_definitions.yaml; Side effects: [fs]; Errors: FileNotFoundError/PermissionError; yaml.YAMLError on malformed YAML
core.state_detector.detect_state_and_overlays(screen) — Returns: {"state": str, "secondary_states": [str], "overlays": [str]} chosen by matching clickmap keys via template matching; Side effects: [cv2], [state], [log]; Errors: RuntimeError when multiple primary states match
