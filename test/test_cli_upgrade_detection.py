#!/usr/bin/env python3
from __future__ import annotations

import json
import numpy as np
from unittest.mock import patch


def test_upgrade_detection_cli_success(tmp_path, capsys):
    from tools.cli import upgrade_detection as cli

    dummy_image = np.zeros((10, 10, 3), dtype=np.uint8)

    with patch("tools.cli.upgrade_detection.cv2.imread", return_value=dummy_image), patch(
        "tools.cli.upgrade_detection.detect_visible_boxes",
        return_value={"left": [], "right": []},
    ), patch("tools.cli.upgrade_detection.annotate_boxes", return_value=dummy_image):
        rc = cli.main(["--image", "dummy.png"])

    assert rc == 0
    out_lines = capsys.readouterr().out.strip().splitlines()
    payload = json.loads(out_lines[-1])
    assert payload["columns"] == {"left": [], "right": []}
