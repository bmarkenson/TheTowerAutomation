"""Command-line helpers for the wave detector pipeline."""

from __future__ import annotations

import argparse

import cv2

from utils.logger import log
from core.ss_capture import capture_adb_screenshot

from .pipeline import (
    FALLBACK_DOT_PATH,
    PRIMARY_DOT_PATH,
    _DEFAULT_MAX_VALUE,
    _DEFAULT_RATE_PER_MIN,
    _DEFAULT_TOLERANCE,
    _save_overlay,
    _tess_info,
    detect_wave_number_from_image,
)


def main() -> None:
    """CLI entrypoint for ad-hoc wave OCR experiments and debugging."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--dot-path", default=PRIMARY_DOT_PATH, help=f"Primary ROI (default {PRIMARY_DOT_PATH})")
    parser.add_argument(
        "--fallback-dot-path",
        default=FALLBACK_DOT_PATH,
        help=f"Fallback ROI (default {FALLBACK_DOT_PATH})",
    )
    parser.add_argument("--image", default=None, help="Path to an image to OCR (else capture via ADB)")
    parser.add_argument("--save-input", default=None, help="Save the raw input image here")
    parser.add_argument(
        "--save-overlay",
        default=None,
        help="Save overlay for the ROI that produced the result (or primary if none)",
    )
    parser.add_argument("--debug-out", default=None, help="Save winner bin image here")
    parser.add_argument("--verbose", action="store_true", help="Print detailed debug info")
    parser.add_argument(
        "--dump-candidates",
        default=None,
        help="Directory to save heavy-sweep variants and OCR probes",
    )
    parser.add_argument(
        "--rate-per-min",
        type=float,
        default=_DEFAULT_RATE_PER_MIN,
        help="Expected waves per minute for time-based scoring",
    )
    parser.add_argument(
        "--tolerance",
        type=int,
        default=_DEFAULT_TOLERANCE,
        help="±window around expected value",
    )
    parser.add_argument(
        "--max-value",
        type=int,
        default=_DEFAULT_MAX_VALUE,
        help="Hard ceiling on accepted wave values",
    )
    args = parser.parse_args()

    if args.image:
        img = cv2.imread(args.image)
        if img is None:
            log(f"Failed to read image at: {args.image}", "ERROR")
            return
    else:
        img = capture_adb_screenshot()
        if img is None:
            raise RuntimeError("Failed to capture screenshot.")

    if args.save_input:
        try:
            cv2.imwrite(args.save_input, img)
            log(f"Saved input image to {args.save_input}", "DEBUG")
        except Exception as exc:
            log(f"Failed to save input image: {exc}", "ERROR")

    if args.verbose:
        log(f"Tesseract info: {_tess_info()}", "DEBUG")

    val, conf = detect_wave_number_from_image(
        img,
        primary_dot_path=args.dot_path,
        fallback_dot_path=args.fallback_dot_path,
        use_heavy=(args.dump_candidates is not None),
        verbose=args.verbose,
        dump_dir=args.dump_candidates,
        debug_out=args.debug_out,
        rate_per_min=args.rate_per_min,
        tolerance=args.tolerance,
        max_value=args.max_value,
    )

    if args.save_overlay:
        _save_overlay(img, args.dot_path, args.save_overlay)

    if val is None:
        print("Wave number: <not detected>")
    else:
        print(f"Wave number: {val} (conf={conf:.1f})")


__all__ = ["main"]
