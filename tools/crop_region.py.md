$PROJECT_ROOT/tools/crop_region.py — Entrypoint
tools.crop_region.reload_image() — Returns: refreshed screenshot in globals (image/clone), resets scroll_offset; Side effects: [adb][cv2][fs]; Errors: Raises RuntimeError when screenshot capture fails.
tools.crop_region.is_coords_only(dot_path) — Returns: True if dot_path is in coords-only groups/prefixes; Side effects: none.
tools.crop_region.save_template_crop_and_entry(x1, y1, x2, y2) — Returns: action result (saves template/region to disk and clickmap; optional gesture logging); Side effects: [cv2][fs][adb][log]; Defaults: prompts for threshold (default 0.90) and roles; Errors: Returns early on invalid input; downstream failures surfaced by called functions.
tools.crop_region.handle_mouse(event, x, y, flags, param) — Returns: action result (updates selection/scroll; may persist region via save_template_crop_and_entry); Side effects: [cv2]; Errors: none material.
