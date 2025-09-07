
tools/crop_region.py
tools.crop_region.reload_image() — R: refreshed screenshot in globals (image/clone), resets scroll_offset; S: [adb][cv2][fs]; E: Raises RuntimeError when screenshot capture fails.
tools.crop_region.is_coords_only(dot_path) — R: True if dot_path is in coords-only groups/prefixes; S: none.
tools.crop_region.save_template_crop_and_entry(x1, y1, x2, y2) — R: action result (saves template/region to disk and clickmap; optional gesture logging); S: [cv2][fs][adb][log]; Defaults: prompts for threshold (default 0.90) and roles; E: Returns early on invalid input; downstream failures surfaced by called functions.
tools.crop_region.handle_mouse(event, x, y, flags, param) — R: action result (updates selection/scroll; may persist region via save_template_crop_and_entry); S: [cv2]; E: none material.
