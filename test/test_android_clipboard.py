from types import SimpleNamespace

import pytest

from core.android_clipboard import (
    decode_clipboard_service_parcel,
    read_battle_report_clipboard,
)


def _service_parcel(text: str) -> str:
    payload = text.encode("utf-16-le")
    if len(payload) % 4:
        payload += b"\x00" * (4 - len(payload) % 4)
    words = [0, len(text)] + [
        int.from_bytes(payload[index : index + 4], "little")
        for index in range(0, len(payload), 4)
    ]
    lines = ["Result: Parcel("]
    for index in range(0, len(words), 4):
        chunk = " ".join(f"{word:08x}" for word in words[index : index + 4])
        lines.append(f"  0x{index * 4:08x}: {chunk} '....'")
    lines.append(")")
    return "\n".join(lines)


def test_clipboard_parcel_decoder_respects_utf16_length_and_word_order():
    report = "Battle Report\nTier\t19\nWave\t4969\nKilled By\tTank\n"

    assert decode_clipboard_service_parcel(_service_parcel(report)) == report


def test_clipboard_parcel_decoder_rejects_unrelated_clipboard_text():
    with pytest.raises(ValueError, match="does not contain a Battle Report"):
        decode_clipboard_service_parcel(_service_parcel("ordinary clipboard text"))


def test_clipboard_reader_uses_verified_service_call_contract():
    report = "Battle Report\nTier\t19\n"
    calls = []

    def shell_fn(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=_service_parcel(report))

    result = read_battle_report_clipboard(
        device_id="localhost:5555",
        shell_fn=shell_fn,
    )

    assert result.success
    assert result.text == report
    assert calls == [
        (
            ["service", "call", "clipboard", "3", "s16", "com.android.shell"],
            {
                "capture_output": True,
                "check": False,
                "device_id": "localhost:5555",
            },
        )
    ]


def test_clipboard_reader_reports_nonzero_service_failure():
    result = read_battle_report_clipboard(
        shell_fn=lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
    )

    assert not result.success
    assert result.reason == "clipboard_service_failed"
