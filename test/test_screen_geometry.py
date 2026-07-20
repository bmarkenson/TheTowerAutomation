from unittest.mock import patch

import pytest

from core.adb_utils import input_swipe, input_tap
from core.screen_geometry import (
    canonical_to_device_point,
    clear_recorded_device_screen_sizes,
    get_device_screen_size,
    record_device_screen_size,
    validate_device_screen_size,
)
from core.screenrecord_frame_stream import ScreenrecordFrameStream


@pytest.fixture(autouse=True)
def _clear_screen_sizes():
    clear_recorded_device_screen_sizes()
    yield
    clear_recorded_device_screen_sizes()


def test_canonical_geometry_remains_identity_until_device_is_observed():
    assert get_device_screen_size(device_id="localhost:5555") == (1080, 1920)
    assert canonical_to_device_point(
        540,
        960,
        device_id="localhost:5555",
    ) == (540, 960)


def test_720p_geometry_scales_and_clamps_canonical_points():
    record_device_screen_size(720, 1280, device_id="localhost:5555")

    assert canonical_to_device_point(
        540,
        960,
        device_id="localhost:5555",
    ) == (360, 640)
    assert canonical_to_device_point(
        1080,
        1920,
        device_id="localhost:5555",
    ) == (719, 1279)


def test_input_helpers_send_scaled_device_coordinates():
    record_device_screen_size(720, 1280, device_id="localhost:5555")

    with patch("core.adb_utils.adb_shell") as adb_shell:
        input_tap(540, 960, device_id="localhost:5555", check=False)
        input_swipe(
            270,
            1440,
            810,
            480,
            260,
            device_id="localhost:5555",
            check=False,
        )

    assert adb_shell.call_args_list[0].args[0] == [
        "input",
        "tap",
        "360",
        "640",
    ]
    assert adb_shell.call_args_list[1].args[0] == [
        "input",
        "swipe",
        "180",
        "960",
        "540",
        "320",
        "260",
    ]
    assert all(
        call.kwargs == {"check": False, "device_id": "localhost:5555"}
        for call in adb_shell.call_args_list
    )


def test_screenrecord_uses_the_observed_native_encoder_size():
    record_device_screen_size(720, 1280, device_id="localhost:5555")

    stream = ScreenrecordFrameStream(device_id="localhost:5555")

    assert stream._size == (720, 1280)


def test_unsupported_geometry_is_rejected_without_replacing_last_observation():
    record_device_screen_size(720, 1280, device_id="localhost:5555")

    with pytest.raises(ValueError, match="supported resolutions"):
        validate_device_screen_size(900, 1600)

    assert get_device_screen_size(device_id="localhost:5555") == (720, 1280)
