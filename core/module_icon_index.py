"""Read-only identification of equipped Ancestral modules from icon artwork."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
from typing import Iterable, Literal, Optional

import cv2
import numpy as np
from numpy.typing import NDArray


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_PATH = ROOT / "config" / "module_icon_index.json"

MatchStatus = Literal["matched", "unknown", "ambiguous", "not_ancestral"]


@dataclass(frozen=True)
class ModuleIconRecord:
    """One name-to-icon mapping in the retained Ancestral catalog."""

    slug: str
    name: str
    family: str
    template_path: Path


@dataclass(frozen=True)
class EquippedModuleSlot:
    """Fixed Modules-overview location, independent of module identity."""

    key: str
    family: str
    role: str
    center: tuple[int, int]


@dataclass(frozen=True)
class ModuleIconCatalog:
    """Validated matching data loaded from ``config/module_icon_index.json``."""

    version: int
    rarity: str
    normalization_size: int
    mask_radius_fraction: float
    alignment_radius: int
    minimum_confidence: float
    minimum_margin: float
    inventory_minimum_confidence: float
    inventory_minimum_margin: float
    green_hsv_lower: tuple[int, int, int]
    green_hsv_upper: tuple[int, int, int]
    minimum_green_fraction: float
    role_crop_sizes: dict[str, int]
    role_frame_crop_sizes: dict[str, int]
    slots: tuple[EquippedModuleSlot, ...]
    modules: tuple[ModuleIconRecord, ...]


@dataclass(frozen=True)
class EquippedModuleMatch:
    """Evidence result for one equipped slot.

    ``name`` and ``slug`` are populated only for an authoritative ``matched``
    result. ``best_candidate`` remains diagnostic evidence for rejected
    unknown or ambiguous observations.
    """

    slot_key: str
    family: str
    role: str
    status: MatchStatus
    name: Optional[str]
    slug: Optional[str]
    confidence: float
    margin: float
    green_fraction: float
    best_candidate: Optional[str]
    runner_up: Optional[str]


def _number(
    value: object,
    *,
    label: str,
    minimum: float,
    maximum: float,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return parsed


def _positive_even_int(value: object, *, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if parsed <= 0 or parsed % 2:
        raise ValueError(f"{label} must be a positive even integer")
    return parsed


def _nonnegative_int(value: object, *, label: str, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if parsed < 0 or parsed > maximum:
        raise ValueError(f"{label} must be between 0 and {maximum}")
    return parsed


def _hsv_triplet(value: object, *, label: str) -> tuple[int, int, int]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{label} must contain three integers")
    try:
        triplet = tuple(int(channel) for channel in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain three integers") from exc
    if any(channel < 0 or channel > 255 for channel in triplet):
        raise ValueError(f"{label} channels must be between 0 and 255")
    return triplet  # type: ignore[return-value]


def load_module_icon_catalog(
    path: Path | str = DEFAULT_CATALOG_PATH,
) -> ModuleIconCatalog:
    """Load and validate the data-driven Ancestral icon catalog."""

    return _load_module_icon_catalog(str(Path(path).resolve()))


@lru_cache(maxsize=4)
def _load_module_icon_catalog(path: str) -> ModuleIconCatalog:
    catalog_path = Path(path)
    with catalog_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("module icon catalog must be a JSON object")

    version = int(raw.get("catalog_version", 0))
    if version != 1:
        raise ValueError(f"unsupported module icon catalog version: {version}")
    rarity = str(raw.get("rarity", "")).strip()
    if rarity != "Ancestral":
        raise ValueError("module icon catalog currently supports only Ancestral")

    normalization = raw.get("normalization")
    authority = raw.get("authority")
    inventory_authority = raw.get("inventory_authority")
    ancestral_green = raw.get("ancestral_green")
    roles = raw.get("roles")
    if not all(
        isinstance(section, dict)
        for section in (
            normalization,
            authority,
            inventory_authority,
            ancestral_green,
            roles,
        )
    ):
        raise ValueError("catalog matching sections must be JSON objects")

    normalization_size = _positive_even_int(
        normalization.get("size"), label="normalization.size"
    )
    mask_radius_fraction = _number(
        normalization.get("mask_radius_fraction"),
        label="normalization.mask_radius_fraction",
        minimum=0.05,
        maximum=0.5,
    )
    alignment_radius = _nonnegative_int(
        normalization.get("alignment_radius"),
        label="normalization.alignment_radius",
        maximum=16,
    )
    minimum_confidence = _number(
        authority.get("minimum_confidence"),
        label="authority.minimum_confidence",
        minimum=-1.0,
        maximum=1.0,
    )
    minimum_margin = _number(
        authority.get("minimum_margin"),
        label="authority.minimum_margin",
        minimum=0.0,
        maximum=2.0,
    )
    inventory_minimum_confidence = _number(
        inventory_authority.get("minimum_confidence"),
        label="inventory_authority.minimum_confidence",
        minimum=-1.0,
        maximum=1.0,
    )
    inventory_minimum_margin = _number(
        inventory_authority.get("minimum_margin"),
        label="inventory_authority.minimum_margin",
        minimum=0.0,
        maximum=2.0,
    )
    green_hsv_lower = _hsv_triplet(
        ancestral_green.get("hsv_lower"), label="ancestral_green.hsv_lower"
    )
    green_hsv_upper = _hsv_triplet(
        ancestral_green.get("hsv_upper"), label="ancestral_green.hsv_upper"
    )
    minimum_green_fraction = _number(
        ancestral_green.get("minimum_fraction"),
        label="ancestral_green.minimum_fraction",
        minimum=0.0,
        maximum=1.0,
    )

    role_crop_sizes: dict[str, int] = {}
    role_frame_crop_sizes: dict[str, int] = {}
    for role, values in roles.items():
        if not isinstance(values, dict):
            raise ValueError(f"roles.{role} must be a JSON object")
        role_crop_sizes[str(role)] = _positive_even_int(
            values.get("crop_size"), label=f"roles.{role}.crop_size"
        )
        role_frame_crop_sizes[str(role)] = _positive_even_int(
            values.get("frame_crop_size"), label=f"roles.{role}.frame_crop_size"
        )

    slots: list[EquippedModuleSlot] = []
    slot_keys: set[str] = set()
    for raw_slot in raw.get("slots", []):
        if not isinstance(raw_slot, dict):
            raise ValueError("each module slot must be a JSON object")
        key = str(raw_slot.get("key", "")).strip()
        family = str(raw_slot.get("family", "")).strip()
        role = str(raw_slot.get("role", "")).strip()
        center = raw_slot.get("center")
        if not key or key in slot_keys:
            raise ValueError(f"module slot key is missing or duplicated: {key!r}")
        if not family or role not in role_crop_sizes:
            raise ValueError(f"invalid family or role for module slot {key}")
        if not isinstance(center, list) or len(center) != 2:
            raise ValueError(f"module slot {key} center must contain x and y")
        try:
            point = (int(center[0]), int(center[1]))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"module slot {key} center must contain integers") from exc
        if point[0] < 0 or point[1] < 0:
            raise ValueError(f"module slot {key} center cannot be negative")
        slot_keys.add(key)
        slots.append(EquippedModuleSlot(key, family, role, point))
    if not slots:
        raise ValueError("module icon catalog has no equipped slots")

    modules: list[ModuleIconRecord] = []
    slugs: set[str] = set()
    names: set[str] = set()
    for raw_module in raw.get("modules", []):
        if not isinstance(raw_module, dict):
            raise ValueError("each module entry must be a JSON object")
        slug = str(raw_module.get("slug", "")).strip()
        name = str(raw_module.get("name", "")).strip()
        family = str(raw_module.get("family", "")).strip()
        raw_template = str(raw_module.get("template", "")).strip()
        if not slug or slug in slugs:
            raise ValueError(f"module slug is missing or duplicated: {slug!r}")
        if not name or name in names:
            raise ValueError(f"module name is missing or duplicated: {name!r}")
        if not family or family not in {slot.family for slot in slots}:
            raise ValueError(f"module {slug} has unknown family {family!r}")
        template_path = Path(raw_template)
        if not template_path.is_absolute():
            template_path = ROOT / template_path
        image = cv2.imread(str(template_path))
        if image is None or image.shape != (200, 200, 3):
            raise ValueError(
                f"module {slug} template must be a readable 200x200 BGR image: "
                f"{template_path}"
            )
        slugs.add(slug)
        names.add(name)
        modules.append(ModuleIconRecord(slug, name, family, template_path))
    if not modules:
        raise ValueError("module icon catalog has no modules")
    for family in {slot.family for slot in slots}:
        if sum(module.family == family for module in modules) < 2:
            raise ValueError(f"module family {family!r} needs at least two candidates")

    return ModuleIconCatalog(
        version=version,
        rarity=rarity,
        normalization_size=normalization_size,
        mask_radius_fraction=mask_radius_fraction,
        alignment_radius=alignment_radius,
        minimum_confidence=minimum_confidence,
        minimum_margin=minimum_margin,
        inventory_minimum_confidence=inventory_minimum_confidence,
        inventory_minimum_margin=inventory_minimum_margin,
        green_hsv_lower=green_hsv_lower,
        green_hsv_upper=green_hsv_upper,
        minimum_green_fraction=minimum_green_fraction,
        role_crop_sizes=role_crop_sizes,
        role_frame_crop_sizes=role_frame_crop_sizes,
        slots=tuple(slots),
        modules=tuple(modules),
    )


def _crop_centered(
    screen: NDArray[np.uint8], center: tuple[int, int], size: int
) -> Optional[NDArray[np.uint8]]:
    half = size // 2
    x0, y0 = center[0] - half, center[1] - half
    x1, y1 = x0 + size, y0 + size
    if x0 < 0 or y0 < 0 or x1 > screen.shape[1] or y1 > screen.shape[0]:
        return None
    return screen[y0:y1, x0:x1]


@lru_cache(maxsize=16)
def _circular_mask(size: int, radius_fraction: float) -> NDArray[np.bool_]:
    y, x = np.ogrid[:size, :size]
    center = (size - 1) / 2.0
    radius = size * radius_fraction
    return (x - center) ** 2 + (y - center) ** 2 <= radius**2


def _unit_feature(
    image: NDArray[np.uint8], *, size: int, radius_fraction: float
) -> Optional[NDArray[np.float32]]:
    normalized = cv2.resize(image, (size, size), interpolation=cv2.INTER_AREA)
    selected = normalized.astype(np.float32)[
        _circular_mask(size, radius_fraction)
    ].reshape(-1)
    selected -= float(selected.mean())
    magnitude = float(np.linalg.norm(selected))
    if magnitude <= 1e-6:
        return None
    return selected / magnitude


@lru_cache(maxsize=128)
def _template_feature(
    path: str, size: int, radius_fraction: float
) -> NDArray[np.float32]:
    image = cv2.imread(path)
    if image is None:
        raise ValueError(f"module icon template became unreadable: {path}")
    feature = _unit_feature(
        image, size=size, radius_fraction=radius_fraction
    )
    if feature is None:
        raise ValueError(f"module icon template has no usable variance: {path}")
    return feature


def _green_fraction(
    image: NDArray[np.uint8], catalog: ModuleIconCatalog
) -> float:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.asarray(catalog.green_hsv_lower, dtype=np.uint8),
        np.asarray(catalog.green_hsv_upper, dtype=np.uint8),
    )
    return float(np.count_nonzero(mask)) / float(mask.size)


def ancestral_green_fraction(
    image: NDArray[np.uint8],
    *,
    catalog: Optional[ModuleIconCatalog] = None,
) -> float:
    """Measure catalog-defined Ancestral-green evidence in an icon frame."""

    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
        return 0.0
    return _green_fraction(image, catalog or load_module_icon_catalog())


def module_icon_similarity(
    image: NDArray[np.uint8],
    module: str,
    *,
    catalog: Optional[ModuleIconCatalog] = None,
) -> float:
    """Return normalized icon correlation for one catalog name or slug."""

    selected_catalog = catalog or load_module_icon_catalog()
    record = next(
        (
            candidate
            for candidate in selected_catalog.modules
            if module in {candidate.name, candidate.slug}
        ),
        None,
    )
    if record is None:
        raise ValueError(f"unknown Ancestral module: {module!r}")
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
        return -1.0
    observed = _unit_feature(
        image,
        size=selected_catalog.normalization_size,
        radius_fraction=selected_catalog.mask_radius_fraction,
    )
    if observed is None:
        return -1.0
    return float(
        observed
        @ _template_feature(
            str(record.template_path),
            selected_catalog.normalization_size,
            selected_catalog.mask_radius_fraction,
        )
    )


def rank_module_icon_candidates(
    images: Iterable[NDArray[np.uint8]],
    *,
    catalog: Optional[ModuleIconCatalog] = None,
) -> tuple[tuple[float, ModuleIconRecord], ...]:
    """Rank catalog identities across aligned observations of one icon."""

    selected_catalog = catalog or load_module_icon_catalog()
    observed_features: list[NDArray[np.float32]] = []
    for image in images:
        if (
            not isinstance(image, np.ndarray)
            or image.ndim != 3
            or image.shape[2] != 3
        ):
            continue
        observed = _unit_feature(
            image,
            size=selected_catalog.normalization_size,
            radius_fraction=selected_catalog.mask_radius_fraction,
        )
        if observed is not None:
            observed_features.append(observed)
    if not observed_features:
        return ()

    observed_matrix = np.stack(observed_features)
    scores = []
    for module in selected_catalog.modules:
        template = _template_feature(
            str(module.template_path),
            selected_catalog.normalization_size,
            selected_catalog.mask_radius_fraction,
        )
        scores.append((float(np.max(observed_matrix @ template)), module))
    scores.sort(key=lambda item: item[0], reverse=True)
    return tuple(scores)


def _rejected_match(
    slot: EquippedModuleSlot,
    *,
    status: MatchStatus,
    green_fraction: float,
) -> EquippedModuleMatch:
    return EquippedModuleMatch(
        slot_key=slot.key,
        family=slot.family,
        role=slot.role,
        status=status,
        name=None,
        slug=None,
        confidence=0.0,
        margin=0.0,
        green_fraction=green_fraction,
        best_candidate=None,
        runner_up=None,
    )


def identify_equipped_ancestral_modules(
    screen: NDArray[np.uint8],
    *,
    catalog: Optional[ModuleIconCatalog] = None,
) -> tuple[EquippedModuleMatch, ...]:
    """Identify all eight equipped modules without taking any action.

    The caller must provide a 1080x1920 Modules-overview BGR frame. Identity is
    authoritative only when the slot has Ancestral-green frame evidence, the
    best same-family icon correlation clears the confidence threshold, and its
    lead over the runner-up clears the ambiguity threshold.
    """

    if not isinstance(screen, np.ndarray) or screen.ndim != 3 or screen.shape[2] != 3:
        raise ValueError("module overview must be a BGR image")
    selected_catalog = catalog or load_module_icon_catalog()
    results: list[EquippedModuleMatch] = []

    for slot in selected_catalog.slots:
        frame_crop = _crop_centered(
            screen,
            slot.center,
            selected_catalog.role_frame_crop_sizes[slot.role],
        )
        if frame_crop is None:
            results.append(
                _rejected_match(slot, status="unknown", green_fraction=0.0)
            )
            continue

        green_fraction = _green_fraction(frame_crop, selected_catalog)
        if green_fraction < selected_catalog.minimum_green_fraction:
            results.append(
                _rejected_match(
                    slot,
                    status="not_ancestral",
                    green_fraction=green_fraction,
                )
            )
            continue

        observed_features: list[NDArray[np.float32]] = []
        for y_offset in range(
            -selected_catalog.alignment_radius,
            selected_catalog.alignment_radius + 1,
        ):
            for x_offset in range(
                -selected_catalog.alignment_radius,
                selected_catalog.alignment_radius + 1,
            ):
                icon_crop = _crop_centered(
                    screen,
                    (
                        slot.center[0] + x_offset,
                        slot.center[1] + y_offset,
                    ),
                    selected_catalog.role_crop_sizes[slot.role],
                )
                if icon_crop is None:
                    continue
                observed = _unit_feature(
                    icon_crop,
                    size=selected_catalog.normalization_size,
                    radius_fraction=selected_catalog.mask_radius_fraction,
                )
                if observed is not None:
                    observed_features.append(observed)
        if not observed_features:
            results.append(
                _rejected_match(
                    slot, status="unknown", green_fraction=green_fraction
                )
            )
            continue

        candidates = [
            module
            for module in selected_catalog.modules
            if module.family == slot.family
        ]
        observed_matrix = np.stack(observed_features)
        scores = []
        for module in candidates:
            template = _template_feature(
                str(module.template_path),
                selected_catalog.normalization_size,
                selected_catalog.mask_radius_fraction,
            )
            scores.append(
                (
                    float(np.max(observed_matrix @ template)),
                    module,
                )
            )
        scores.sort(key=lambda item: item[0], reverse=True)
        best_score, best = scores[0]
        runner_score, runner = scores[1]
        margin = best_score - runner_score
        if best_score < selected_catalog.minimum_confidence:
            status: MatchStatus = "unknown"
        elif margin < selected_catalog.minimum_margin:
            status = "ambiguous"
        else:
            status = "matched"

        results.append(
            EquippedModuleMatch(
                slot_key=slot.key,
                family=slot.family,
                role=slot.role,
                status=status,
                name=best.name if status == "matched" else None,
                slug=best.slug if status == "matched" else None,
                confidence=best_score,
                margin=margin,
                green_fraction=green_fraction,
                best_candidate=best.name,
                runner_up=runner.name,
            )
        )

    return tuple(results)


__all__ = [
    "DEFAULT_CATALOG_PATH",
    "EquippedModuleMatch",
    "EquippedModuleSlot",
    "ModuleIconCatalog",
    "ModuleIconRecord",
    "ancestral_green_fraction",
    "identify_equipped_ancestral_modules",
    "load_module_icon_catalog",
    "module_icon_similarity",
    "rank_module_icon_candidates",
]
