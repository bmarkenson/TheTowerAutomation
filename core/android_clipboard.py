"""Read Android clipboard text through the platform clipboard service."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Optional

from core.adb_utils import adb_shell


_CLIPBOARD_COMMAND = ["service", "call", "clipboard", "3", "s16", "com.android.shell"]
_PARCEL_LINE = re.compile(r"^\s*0x[0-9a-fA-F]+:\s*(.*)$")
_HEX_WORD = re.compile(r"\b[0-9a-fA-F]{8}\b")


@dataclass(frozen=True)
class ClipboardReadResult:
    """One best-effort Android clipboard read."""

    text: Optional[str]
    reason: str

    @property
    def success(self) -> bool:
        return self.text is not None


def decode_clipboard_service_parcel(output: str) -> str:
    """Decode the UTF-16 string returned by ``service call clipboard``.

    Android's ``service call`` printer renders each Parcel word as a 32-bit
    hexadecimal integer. String payloads are UTF-16LE, so the low 16-bit code
    unit in each printed word precedes the high 16-bit unit. The word directly
    before the payload contains its UTF-16 code-unit length.

    The clipboard Parcel contains other strings (for example ``Text`` and
    ``text/plain``), so this decoder deliberately selects the Stats payload by
    its stable ``Battle Report`` prefix and fails closed for unrelated content.
    """

    words: list[int] = []
    for line in (output or "").splitlines():
        match = _PARCEL_LINE.match(line)
        if not match:
            continue
        # Ignore the quoted ASCII visualization at the end of each data line.
        hexadecimal = match.group(1).split("'", 1)[0]
        words.extend(int(word, 16) for word in _HEX_WORD.findall(hexadecimal))

    prefix = "Battle Report\n".encode("utf-16-le")
    for index in range(1, len(words)):
        available_units = (len(words) - index) * 2
        unit_count = words[index - 1]
        if unit_count <= 0 or unit_count > available_units:
            continue
        preview = b"".join(word.to_bytes(4, "little") for word in words[index : index + 8])
        if not preview.startswith(prefix):
            continue
        payload = b"".join(word.to_bytes(4, "little") for word in words[index:])
        try:
            return payload[: unit_count * 2].decode("utf-16-le")
        except UnicodeDecodeError as exc:
            raise ValueError("clipboard Parcel contains invalid UTF-16") from exc

    raise ValueError("clipboard does not contain a Battle Report payload")


def read_battle_report_clipboard(
    *,
    device_id: Optional[str] = None,
    shell_fn: Callable = adb_shell,
) -> ClipboardReadResult:
    """Read a copied Battle Report from the current Android target."""

    result = shell_fn(
        _CLIPBOARD_COMMAND,
        capture_output=True,
        check=False,
        device_id=device_id,
    )
    if result is None:
        return ClipboardReadResult(None, "adb_command_failed")
    if getattr(result, "returncode", 1) != 0:
        return ClipboardReadResult(None, "clipboard_service_failed")
    try:
        text = decode_clipboard_service_parcel(str(getattr(result, "stdout", "") or ""))
    except ValueError as exc:
        return ClipboardReadResult(None, str(exc))
    return ClipboardReadResult(text, "battle_report")


__all__ = [
    "ClipboardReadResult",
    "decode_clipboard_service_parcel",
    "read_battle_report_clipboard",
]
