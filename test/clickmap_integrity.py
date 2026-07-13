#!/usr/bin/env python3
"""Recursively audit clickmap entries and template assets."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Tuple

import cv2


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.clickmap_access import get_clickmap


TEMPLATE_DIR = ROOT / "assets" / "match_templates"
SCREEN_SIZE = (1080, 1920)
ENTRY_KEYS = {
    "roles",
    "match_template",
    "match_region",
    "region_ref",
    "tap",
    "swipe",
    "match_threshold",
    "match_padding",
}


@dataclass
class AuditReport:
    entries: int = 0
    templates_referenced: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)


def iter_entries(
    node: Mapping[str, Any],
    prefix: str = "",
) -> Iterator[Tuple[str, Mapping[str, Any]]]:
    """Yield every nested mapping that has clickmap entry semantics."""

    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if not isinstance(value, Mapping):
            continue
        if ENTRY_KEYS.intersection(value):
            yield path, value
        yield from iter_entries(value, path)


def _validate_int_fields(
    path: str,
    block_name: str,
    block: Any,
    required: Iterable[str],
    report: AuditReport,
) -> bool:
    if not isinstance(block, Mapping):
        report.errors.append(f"{path}: {block_name} must be an object")
        return False
    valid = True
    for key in required:
        if key not in block:
            report.errors.append(f"{path}: {block_name}.{key} is missing")
            valid = False
        elif not isinstance(block[key], int) or isinstance(block[key], bool):
            report.errors.append(f"{path}: {block_name}.{key} must be an integer")
            valid = False
    return valid


def _resolve_region(
    path: str,
    entry: Mapping[str, Any],
    clickmap: Mapping[str, Any],
    report: AuditReport,
) -> Optional[Mapping[str, Any]]:
    region = entry.get("match_region")
    if region is not None:
        return region if isinstance(region, Mapping) else None
    region_ref = entry.get("region_ref")
    if region_ref is None:
        return None
    shared = clickmap.get("_shared_match_regions")
    shared_entry = shared.get(region_ref) if isinstance(shared, Mapping) else None
    if not isinstance(shared_entry, Mapping):
        report.errors.append(f"{path}: dangling region_ref {region_ref!r}")
        return None
    resolved = shared_entry.get("match_region")
    if not isinstance(resolved, Mapping):
        report.errors.append(
            f"{path}: shared region {region_ref!r} has no match_region"
        )
        return None
    return resolved


def _validate_region(
    path: str,
    region: Any,
    report: AuditReport,
) -> bool:
    if not _validate_int_fields(
        path,
        "match_region",
        region,
        ("x", "y", "w", "h"),
        report,
    ):
        return False
    x, y, w, h = (int(region[key]) for key in ("x", "y", "w", "h"))
    screen_w, screen_h = SCREEN_SIZE
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        report.errors.append(f"{path}: match_region has non-positive/out-of-bounds origin")
        return False
    if x + w > screen_w or y + h > screen_h:
        report.errors.append(
            f"{path}: match_region ({x}, {y}, {w}, {h}) exceeds {screen_w}x{screen_h}"
        )
        return False
    return True


def _effective_region_size(
    region: Mapping[str, Any],
    padding: int,
) -> Tuple[int, int]:
    screen_w, screen_h = SCREEN_SIZE
    x, y, w, h = (int(region[key]) for key in ("x", "y", "w", "h"))
    x1 = max(0, x - padding)
    y1 = max(0, y - padding)
    x2 = min(screen_w, x + w + padding)
    y2 = min(screen_h, y + h + padding)
    return x2 - x1, y2 - y1


def validate_entry(
    path: str,
    entry: Mapping[str, Any],
    clickmap: Mapping[str, Any],
    report: AuditReport,
) -> None:
    report.entries += 1

    roles = entry.get("roles")
    if roles is not None and (
        not isinstance(roles, list)
        or not roles
        or not all(isinstance(role, str) and role for role in roles)
    ):
        report.errors.append(f"{path}: roles must be a non-empty string list")

    region = _resolve_region(path, entry, clickmap, report)
    region_valid = False
    if entry.get("match_region") is not None or entry.get("region_ref") is not None:
        if region is None and entry.get("match_region") is not None:
            report.errors.append(f"{path}: match_region must be an object")
        elif region is not None:
            region_valid = _validate_region(path, region, report)

    if "tap" in entry:
        if _validate_int_fields(path, "tap", entry["tap"], ("x", "y"), report):
            x, y = int(entry["tap"]["x"]), int(entry["tap"]["y"])
            if not (0 <= x < SCREEN_SIZE[0] and 0 <= y < SCREEN_SIZE[1]):
                report.errors.append(f"{path}: tap coordinate ({x}, {y}) is off-screen")

    if "swipe" in entry:
        required = ("x1", "y1", "x2", "y2", "duration_ms")
        if _validate_int_fields(path, "swipe", entry["swipe"], required, report):
            swipe = entry["swipe"]
            for x_key, y_key in (("x1", "y1"), ("x2", "y2")):
                x, y = int(swipe[x_key]), int(swipe[y_key])
                if not (0 <= x < SCREEN_SIZE[0] and 0 <= y < SCREEN_SIZE[1]):
                    report.errors.append(
                        f"{path}: swipe point {x_key}/{y_key}=({x}, {y}) is off-screen"
                    )
            if int(swipe["duration_ms"]) <= 0:
                report.errors.append(f"{path}: swipe.duration_ms must be positive")

    threshold = entry.get("match_threshold")
    if threshold is not None:
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            report.errors.append(f"{path}: match_threshold must be numeric")
        elif not 0.0 < float(threshold) <= 1.0:
            report.errors.append(f"{path}: match_threshold must be in (0, 1]")

    padding = entry.get("match_padding", 12)
    if isinstance(padding, bool) or not isinstance(padding, int) or padding < 0:
        report.errors.append(f"{path}: match_padding must be a non-negative integer")
        padding = 0

    template_ref = entry.get("match_template")
    if template_ref is None:
        return
    if not isinstance(template_ref, str) or not template_ref:
        report.errors.append(f"{path}: match_template must be a non-empty string")
        return
    report.templates_referenced.add(template_ref)
    template_path = TEMPLATE_DIR / template_ref
    if not template_path.is_file():
        report.errors.append(f"{path}: missing template {template_ref}")
        return
    template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
    if template is None:
        report.errors.append(f"{path}: unreadable template {template_ref}")
        return
    if region is None:
        report.errors.append(f"{path}: template has no match_region or region_ref")
        return
    if region_valid:
        search_w, search_h = _effective_region_size(region, int(padding))
        template_h, template_w = template.shape[:2]
        if template_w > search_w or template_h > search_h:
            report.errors.append(
                f"{path}: template {template_w}x{template_h} exceeds effective "
                f"search region {search_w}x{search_h}"
            )


def audit_clickmap(clickmap: Optional[Mapping[str, Any]] = None) -> AuditReport:
    data = clickmap or get_clickmap()
    report = AuditReport()
    for path, entry in iter_entries(data):
        validate_entry(path, entry, data, report)

    assets = {
        path.relative_to(TEMPLATE_DIR).as_posix()
        for path in TEMPLATE_DIR.rglob("*")
        if path.is_file()
    }
    report.orphans = sorted(assets - report.templates_referenced)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--show-orphans",
        action="store_true",
        help="List every template asset not referenced by clickmap.json",
    )
    parser.add_argument(
        "--strict-orphans",
        action="store_true",
        help="Treat orphaned template assets as a validation failure",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = audit_clickmap()

    for error in report.errors:
        print(f"[FAIL] {error}")
    for warning in report.warnings:
        print(f"[WARN] {warning}")
    if args.show_orphans:
        for orphan in report.orphans:
            print(f"[ORPHAN] {orphan}")

    print(
        "[SUMMARY] "
        f"entries={report.entries} "
        f"referenced_templates={len(report.templates_referenced)} "
        f"errors={len(report.errors)} "
        f"orphans={len(report.orphans)}"
    )
    if report.errors or (args.strict_orphans and report.orphans):
        return 1
    print("[PASS] Recursive clickmap/template integrity checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
