
core/state_detector.py
core.state_detector.load_state_definitions() — R: dict parsed from config/state_definitions.yaml; S: [fs]; E: FileNotFoundError/PermissionError; yaml.YAMLError on malformed YAML
core.state_detector.detect_state_and_overlays(screen) — R: {"state": str, "secondary_states": [str], "overlays": [str]} chosen by matching clickmap keys via template matching; S: [cv2], [state], [log]; E: RuntimeError when multiple primary states match
