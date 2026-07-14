#!/usr/bin/env python3
"""Headless, dry-run-first template creation and validation workflow."""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile
from typing import Any, Iterable, Mapping, Optional, Sequence

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.clickmap_access import resolve_dot_path
from core.matcher import match_entry_result, normalize_region, resolve_match_region
from core.ss_capture import capture_and_save_screenshot


DEFAULT_CLICKMAP_PATH = ROOT / "config" / "clickmap.json"
DEFAULT_TEMPLATE_DIR = ROOT / "assets" / "match_templates"
PROFILE_OPTIONS = ("detector", "label")


class WorkflowError(ValueError):
    """A guarded template workflow failure suitable for CLI display."""


@dataclass
class TemplatePlan:
    dot_path: str
    source_path: Path
    source: np.ndarray
    crop_region: dict[str, int]
    crop: np.ndarray
    template_ref: str
    template_path: Path
    entry: dict[str, Any]
    clickmap_before: dict[str, Any]
    clickmap_after: dict[str, Any]
    clickmap_path: Path
    existing_entry: bool
    shared_template_references: list[str] = field(default_factory=list)
    asset_comparison: Optional[dict[str, Any]] = None
    warnings: list[str] = field(default_factory=list)


def parse_region(value: str) -> dict[str, int]:
    """Parse ``x,y,w,h`` CLI syntax."""

    try:
        values = [int(part.strip()) for part in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("region values must be integers") from exc
    if len(values) != 4:
        raise argparse.ArgumentTypeError("region must use x,y,w,h")
    x, y, w, h = values
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError(
            "region x/y must be non-negative and w/h must be positive"
        )
    return {"x": x, "y": y, "w": w, "h": h}


def parse_roles(value: str) -> list[str]:
    roles = [part.strip() for part in value.split(",") if part.strip()]
    if not roles:
        raise argparse.ArgumentTypeError("roles must contain at least one value")
    return roles


def derive_template_ref(dot_path: str) -> str:
    """Derive the conventional PNG path for a clickmap dot path."""

    parts = dot_path.split(".")
    if len(parts) < 2 or any(not part for part in parts):
        raise WorkflowError("dot path must contain at least one group and entry key")
    if any("/" in part or "\\" in part for part in parts):
        raise WorkflowError("dot path segments cannot contain path separators")
    return PurePosixPath(*parts[:-1], f"{parts[-1]}.png").as_posix()


def resolve_template_path(template_dir: Path, template_ref: str) -> Path:
    """Resolve and guard a template reference beneath the asset directory."""

    ref = PurePosixPath(template_ref)
    if ref.is_absolute() or ".." in ref.parts or ref.suffix.lower() != ".png":
        raise WorkflowError("template reference must be a relative PNG path")
    target = (template_dir / Path(*ref.parts)).resolve()
    base = template_dir.resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise WorkflowError("template reference escapes the template directory") from exc
    return target


def load_clickmap(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"could not load clickmap {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkflowError(f"clickmap root must be an object: {path}")
    return payload


def _set_dot_path(mapping: dict[str, Any], dot_path: str, value: Any) -> None:
    parts = dot_path.split(".")
    current = mapping
    for segment in parts[:-1]:
        child = current.get(segment)
        if child is None:
            child = {}
            current[segment] = child
        if not isinstance(child, dict):
            raise WorkflowError(
                f"cannot create {dot_path!r}: parent {segment!r} is not an object"
            )
        current = child
    current[parts[-1]] = value


def _template_references(
    node: Mapping[str, Any],
    template_ref: str,
    prefix: str = "",
) -> list[str]:
    references: list[str] = []
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if not isinstance(value, Mapping):
            continue
        if value.get("match_template") == template_ref:
            references.append(path)
        references.extend(_template_references(value, template_ref, path))
    return references


def _load_image(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise WorkflowError(f"could not read image {path}")
    return image


def _crop_image(image: np.ndarray, region: Mapping[str, int]) -> np.ndarray:
    x, y, w, h = (int(region[key]) for key in ("x", "y", "w", "h"))
    image_h, image_w = image.shape[:2]
    if x + w > image_w or y + h > image_h:
        raise WorkflowError(
            f"crop ({x}, {y}, {w}, {h}) exceeds image {image_w}x{image_h}"
        )
    crop = image[y : y + h, x : x + w].copy()
    if not crop.size:
        raise WorkflowError("crop is empty")
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    stddev = float(np.std(gray))
    if stddev <= 1e-6:
        raise WorkflowError(
            "crop is constant; TM_CCOEFF_NORMED cannot produce a reliable template"
        )
    return crop


def _build_entry(
    *,
    dot_path: str,
    clickmap: Mapping[str, Any],
    crop_region: Mapping[str, int],
    template_ref: Optional[str],
    match_region: Optional[Mapping[str, int]],
    region_ref: Optional[str],
    threshold: Optional[float],
    padding: Optional[int],
    roles: Optional[Sequence[str]],
) -> tuple[dict[str, Any], bool, str]:
    existing_value = resolve_dot_path(dot_path, clickmap)
    if existing_value is not None and not isinstance(existing_value, Mapping):
        raise WorkflowError(f"existing clickmap value at {dot_path!r} is not an object")
    existing = copy.deepcopy(dict(existing_value or {}))
    existing_entry = existing_value is not None

    chosen_template = template_ref or existing.get("match_template")
    if not chosen_template:
        chosen_template = derive_template_ref(dot_path)
    existing["match_template"] = chosen_template

    if region_ref:
        shared = resolve_dot_path(f"_shared_match_regions.{region_ref}", clickmap)
        if not isinstance(shared, Mapping):
            raise WorkflowError(f"unknown shared region {region_ref!r}")
        normalize_region(shared)
        existing.pop("match_region", None)
        existing["region_ref"] = region_ref
    elif match_region is not None:
        existing.pop("region_ref", None)
        existing["match_region"] = dict(match_region)
    elif "match_region" not in existing and "region_ref" not in existing:
        existing["match_region"] = dict(crop_region)

    chosen_threshold = (
        float(threshold)
        if threshold is not None
        else float(existing.get("match_threshold", 0.9))
    )
    if not 0.0 < chosen_threshold <= 1.0:
        raise WorkflowError("match threshold must be in (0, 1]")
    existing["match_threshold"] = chosen_threshold

    if padding is not None:
        if padding < 0:
            raise WorkflowError("match padding must be non-negative")
        existing["match_padding"] = int(padding)

    if roles is not None:
        existing["roles"] = list(roles)
    existing_roles = existing.get("roles")
    if not isinstance(existing_roles, list) or not existing_roles:
        raise WorkflowError("new or unclassified entries require --roles")

    # Resolve now so a dangling or malformed existing region fails before any
    # candidate file or clickmap mutation is attempted.
    resolve_match_region(existing, clickmap)
    return existing, existing_entry, str(chosen_template)


def build_plan(
    *,
    source_path: Path,
    dot_path: str,
    crop_region: Mapping[str, int],
    clickmap_path: Path = DEFAULT_CLICKMAP_PATH,
    template_dir: Path = DEFAULT_TEMPLATE_DIR,
    template_ref: Optional[str] = None,
    match_region: Optional[Mapping[str, int]] = None,
    region_ref: Optional[str] = None,
    threshold: Optional[float] = None,
    padding: Optional[int] = None,
    roles: Optional[Sequence[str]] = None,
) -> TemplatePlan:
    source_path = source_path.resolve()
    source = _load_image(source_path)
    crop_box = normalize_region(crop_region)
    crop = _crop_image(source, crop_box)
    clickmap = load_clickmap(clickmap_path)
    entry, existing_entry, chosen_ref = _build_entry(
        dot_path=dot_path,
        clickmap=clickmap,
        crop_region=crop_box,
        template_ref=template_ref,
        match_region=match_region,
        region_ref=region_ref,
        threshold=threshold,
        padding=padding,
        roles=roles,
    )
    template_path = resolve_template_path(template_dir, chosen_ref)
    clickmap_after = copy.deepcopy(clickmap)
    _set_dot_path(clickmap_after, dot_path, entry)
    shared = [
        path
        for path in _template_references(clickmap, chosen_ref)
        if path != dot_path
    ]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    stddev = float(np.std(gray))
    warnings: list[str] = []
    asset_comparison: Optional[dict[str, Any]] = None
    if template_path.exists():
        existing_template = _load_image(template_path)
        existing_h, existing_w = existing_template.shape[:2]
        candidate_h, candidate_w = crop.shape[:2]
        same_dimensions = (existing_w, existing_h) == (candidate_w, candidate_h)
        asset_comparison = {
            "existing_dimensions": [existing_w, existing_h],
            "candidate_dimensions": [candidate_w, candidate_h],
            "same_dimensions": same_dimensions,
            "differing_pixels": None,
            "rmse": None,
        }
        if same_dimensions:
            delta = crop.astype(np.float32) - existing_template.astype(np.float32)
            asset_comparison["differing_pixels"] = int(
                np.count_nonzero(np.any(delta != 0, axis=2))
            )
            asset_comparison["rmse"] = round(float(np.sqrt(np.mean(delta**2))), 6)
        else:
            warnings.append(
                "candidate dimensions differ from the existing asset: "
                f"{candidate_w}x{candidate_h} vs {existing_w}x{existing_h}"
            )
    if stddev < 5.0:
        warnings.append(
            f"template grayscale standard deviation is low ({stddev:.2f}); "
            "independent positive and negative fixtures are strongly recommended"
        )
    if shared:
        warnings.append(
            "template asset is also referenced by: " + ", ".join(shared)
        )
    return TemplatePlan(
        dot_path=dot_path,
        source_path=source_path,
        source=source,
        crop_region=crop_box,
        crop=crop,
        template_ref=chosen_ref,
        template_path=template_path,
        entry=entry,
        clickmap_before=clickmap,
        clickmap_after=clickmap_after,
        clickmap_path=clickmap_path,
        existing_entry=existing_entry,
        shared_template_references=shared,
        asset_comparison=asset_comparison,
        warnings=warnings,
    )


def _profile_names(profile: str) -> tuple[str, ...]:
    if profile == "both":
        return PROFILE_OPTIONS
    if profile not in PROFILE_OPTIONS:
        raise WorkflowError(f"unknown match profile {profile!r}")
    return (profile,)


def _write_candidate(root: Path, plan: TemplatePlan) -> None:
    candidate_path = resolve_template_path(root, plan.template_ref)
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(candidate_path), plan.crop):
        raise WorkflowError(f"failed to write staged template {candidate_path}")


def validate_plan(
    plan: TemplatePlan,
    *,
    profile: str = "both",
    positive_paths: Iterable[Path] = (),
    negative_paths: Iterable[Path] = (),
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate a staged template against source, positives, and negatives."""

    records: list[dict[str, Any]] = []
    errors: list[str] = []
    sources: list[tuple[str, Path, np.ndarray, Optional[tuple[int, int, int, int]]]] = [
        (
            "source",
            plan.source_path,
            plan.source,
            tuple(plan.crop_region[key] for key in ("x", "y", "w", "h")),
        )
    ]
    sources.extend(
        ("positive", Path(path).resolve(), _load_image(Path(path).resolve()), None)
        for path in positive_paths
    )
    sources.extend(
        ("negative", Path(path).resolve(), _load_image(Path(path).resolve()), None)
        for path in negative_paths
    )

    with tempfile.TemporaryDirectory(prefix="thetower-template-stage-") as temp_dir:
        stage_dir = Path(temp_dir)
        _write_candidate(stage_dir, plan)
        for kind, image_path, image, expected_bbox in sources:
            for profile_name in _profile_names(profile):
                grayscale = profile_name == "label"
                padding = 0 if grayscale else None
                result = match_entry_result(
                    image,
                    plan.entry,
                    template_dir=stage_dir,
                    grayscale=grayscale,
                    padding=padding,
                    clickmap=plan.clickmap_after,
                )
                bbox = list(result.bbox) if result.bbox is not None else None
                passed = result.failure_reason is None
                if kind in {"source", "positive"}:
                    passed = passed and result.matched
                    if expected_bbox is not None:
                        passed = passed and result.bbox == expected_bbox
                else:
                    passed = passed and not result.matched
                record = {
                    "kind": kind,
                    "image": str(image_path),
                    "profile": profile_name,
                    "passed": bool(passed),
                    "matched": result.matched,
                    "confidence": round(result.confidence, 6),
                    "threshold": result.threshold,
                    "bbox": bbox,
                    "center": list(result.center) if result.center is not None else None,
                    "search_region": (
                        list(result.search_region)
                        if result.search_region is not None
                        else None
                    ),
                    "failure_reason": result.failure_reason,
                }
                records.append(record)
                if not passed:
                    details = result.failure_reason or (
                        f"confidence={result.confidence:.3f}, bbox={result.bbox}"
                    )
                    errors.append(
                        f"{kind} validation failed for {profile_name} on "
                        f"{image_path}: {details}"
                    )
    return records, errors


def write_preview(
    plan: TemplatePlan,
    records: Sequence[Mapping[str, Any]],
    errors: Sequence[str],
    preview_dir: Optional[Path] = None,
) -> dict[str, str]:
    """Write the candidate, annotated source, and machine-readable plan."""

    if preview_dir is None:
        slug = plan.dot_path.replace(".", "_").replace(":", "_")
        preview_dir = Path(tempfile.mkdtemp(prefix=f"thetower-template-{slug}-"))
    else:
        preview_dir = preview_dir.resolve()
        preview_dir.mkdir(parents=True, exist_ok=True)

    candidate_path = preview_dir / "candidate.png"
    annotated_path = preview_dir / "annotated_source.png"
    report_path = preview_dir / "plan.json"
    if not cv2.imwrite(str(candidate_path), plan.crop):
        raise WorkflowError(f"failed to write preview candidate {candidate_path}")

    annotated = plan.source.copy()
    crop = plan.crop_region
    cv2.rectangle(
        annotated,
        (crop["x"], crop["y"]),
        (crop["x"] + crop["w"], crop["y"] + crop["h"]),
        (0, 255, 0),
        2,
    )
    search = resolve_match_region(plan.entry, plan.clickmap_after)
    cv2.rectangle(
        annotated,
        (search["x"], search["y"]),
        (search["x"] + search["w"], search["y"] + search["h"]),
        (0, 255, 255),
        2,
    )
    cv2.putText(
        annotated,
        f"crop: {plan.dot_path}",
        (max(0, crop["x"]), max(20, crop["y"] - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )
    source_records = [record for record in records if record.get("kind") == "source"]
    profile_colors = {"detector": (255, 0, 0), "label": (255, 0, 255)}
    for record in source_records:
        bbox = record.get("bbox")
        if not bbox:
            continue
        x, y, w, h = (int(value) for value in bbox)
        color = profile_colors.get(str(record.get("profile")), (255, 255, 255))
        cv2.rectangle(annotated, (x, y), (x + w, y + h), color, 1)
    if not cv2.imwrite(str(annotated_path), annotated):
        raise WorkflowError(f"failed to write annotated preview {annotated_path}")

    report = {
        "dot_path": plan.dot_path,
        "source": str(plan.source_path),
        "crop_region": plan.crop_region,
        "template_ref": plan.template_ref,
        "template_path": str(plan.template_path),
        "entry": plan.entry,
        "existing_entry": plan.existing_entry,
        "shared_template_references": plan.shared_template_references,
        "asset_comparison": plan.asset_comparison,
        "warnings": plan.warnings,
        "errors": list(errors),
        "validations": list(records),
    }
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "candidate": str(candidate_path),
        "annotated_source": str(annotated_path),
        "plan": str(report_path),
    }


def _stage_bytes(target: Path, payload: bytes) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
        delete=False,
    ) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        return Path(handle.name)


def commit_plan(
    plan: TemplatePlan,
    *,
    replace: bool = False,
    allow_shared_template: bool = False,
    allow_size_change: bool = False,
) -> None:
    """Atomically stage and commit the template plus clickmap update."""

    if plan.existing_entry and not replace:
        raise WorkflowError("existing clickmap entry requires --replace")
    if plan.template_path.exists() and not replace:
        raise WorkflowError("existing template asset requires --replace")
    if plan.shared_template_references and not allow_shared_template:
        raise WorkflowError(
            "template is shared; use --allow-shared-template only after reviewing "
            "all affected entries"
        )
    if (
        plan.asset_comparison
        and not plan.asset_comparison["same_dimensions"]
        and not allow_size_change
    ):
        raise WorkflowError(
            "candidate dimensions differ from the existing asset; use "
            "--allow-size-change only after reviewing the search geometry"
        )

    ok, encoded = cv2.imencode(".png", plan.crop)
    if not ok:
        raise WorkflowError("OpenCV failed to encode the template PNG")
    clickmap_bytes = (
        json.dumps(plan.clickmap_after, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    old_template = plan.template_path.read_bytes() if plan.template_path.exists() else None
    old_clickmap = plan.clickmap_path.read_bytes()
    template_stage: Optional[Path] = None
    clickmap_stage: Optional[Path] = None
    template_replaced = False
    clickmap_replaced = False
    try:
        template_stage = _stage_bytes(plan.template_path, encoded.tobytes())
        clickmap_stage = _stage_bytes(plan.clickmap_path, clickmap_bytes)
        template_stage.replace(plan.template_path)
        template_replaced = True
        clickmap_stage.replace(plan.clickmap_path)
        clickmap_replaced = True
    except Exception:
        if template_replaced:
            if old_template is None:
                plan.template_path.unlink(missing_ok=True)
            else:
                plan.template_path.write_bytes(old_template)
        if clickmap_replaced:
            plan.clickmap_path.write_bytes(old_clickmap)
        raise
    finally:
        if template_stage is not None:
            template_stage.unlink(missing_ok=True)
        if clickmap_stage is not None:
            clickmap_stage.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path, help="Existing source screenshot")
    source.add_argument(
        "--capture",
        type=Path,
        metavar="PATH",
        help="Capture a fresh ADB screenshot to PATH and use it",
    )
    parser.add_argument("--dot-path", required=True, help="Clickmap entry dot path")
    parser.add_argument("--crop", required=True, type=parse_region, help="x,y,w,h")
    region = parser.add_mutually_exclusive_group()
    region.add_argument(
        "--match-region",
        type=parse_region,
        help="Search region x,y,w,h; defaults to existing region or crop",
    )
    region.add_argument("--region-ref", help="Existing shared-region name")
    parser.add_argument("--template-ref", help="Relative PNG path under template dir")
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--padding", type=int)
    parser.add_argument("--roles", type=parse_roles, help="Comma-separated role list")
    parser.add_argument(
        "--profile",
        choices=("both",) + PROFILE_OPTIONS,
        default="both",
        help="Runtime compatibility profile(s) to validate (default: both)",
    )
    parser.add_argument(
        "--positive",
        action="append",
        type=Path,
        default=[],
        help="Additional screenshot where the template must match; repeatable",
    )
    parser.add_argument(
        "--negative",
        action="append",
        type=Path,
        default=[],
        help="Screenshot where the template must not match; repeatable",
    )
    parser.add_argument("--preview-dir", type=Path)
    parser.add_argument("--clickmap", type=Path, default=DEFAULT_CLICKMAP_PATH)
    parser.add_argument("--template-dir", type=Path, default=DEFAULT_TEMPLATE_DIR)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Allow replacing an existing entry and template asset",
    )
    parser.add_argument(
        "--allow-shared-template",
        action="store_true",
        help="Allow replacing an asset referenced by other clickmap entries",
    )
    parser.add_argument(
        "--allow-size-change",
        action="store_true",
        help="Allow an existing template asset's dimensions to change",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.capture is not None:
            source_path = args.capture.resolve()
            if capture_and_save_screenshot(source_path) is None:
                raise WorkflowError("ADB screenshot capture failed")
        else:
            source_path = args.image.resolve()

        plan = build_plan(
            source_path=source_path,
            dot_path=args.dot_path,
            crop_region=args.crop,
            clickmap_path=args.clickmap.resolve(),
            template_dir=args.template_dir.resolve(),
            template_ref=args.template_ref,
            match_region=args.match_region,
            region_ref=args.region_ref,
            threshold=args.threshold,
            padding=args.padding,
            roles=args.roles,
        )
        records, errors = validate_plan(
            plan,
            profile=args.profile,
            positive_paths=args.positive,
            negative_paths=args.negative,
        )
        previews = write_preview(plan, records, errors, args.preview_dir)
        if errors:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "errors": errors,
                        "asset_comparison": plan.asset_comparison,
                        "warnings": plan.warnings,
                        "validations": records,
                        "previews": previews,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 1
        if args.commit:
            try:
                commit_plan(
                    plan,
                    replace=args.replace,
                    allow_shared_template=args.allow_shared_template,
                    allow_size_change=args.allow_size_change,
                )
            except WorkflowError as exc:
                print(
                    json.dumps(
                        {
                            "status": "error",
                            "error": str(exc),
                            "asset_comparison": plan.asset_comparison,
                            "warnings": plan.warnings,
                            "validations": records,
                            "previews": previews,
                        },
                        indent=2,
                        ensure_ascii=False,
                    )
                )
                return 1
        output = {
            "status": "committed" if args.commit else "dry-run",
            "dot_path": plan.dot_path,
            "template_ref": plan.template_ref,
            "existing_entry": plan.existing_entry,
            "asset_comparison": plan.asset_comparison,
            "warnings": plan.warnings,
            "validations": records,
            "previews": previews,
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return 0
    except (WorkflowError, FileNotFoundError, OSError, cv2.error) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
