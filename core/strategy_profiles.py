"""Validated custom strategy profiles shared by the runtime and control surface."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Callable, Iterator, Mapping, Optional
import uuid

import yaml

from core.control_model import validate_setup_capture_preview
from core.gate_decisions import (
    PROFILE_SKIPPABLE_CHECKS,
    STARTUP_GATE_CHECK_LABELS,
    normalize_profile_skip_checks,
)
from core.perk_configuration import (
    PERK_CONFIGURATION_LABELS,
    normalize_perk_configuration_requirements,
    normalize_perk_first_choice_requirement,
)
from core.module_presets import (
    DEFAULT_CUSTOM_MODULE_PRESET_DIRECTORY,
    ModulePresetError,
    ModulePresetStore,
    module_preset_definitions,
)
from core.player_save_setup_capture import (
    SetupCaptureError,
    strategy_source_from_capture,
)
from core.strategy_authoring import (
    AUTHORING_SCHEMA_VERSION,
    FARM_SETTING_REGISTRY,
    LEGACY_AUTHORING_SCHEMA_VERSION,
    StrategyAuthoringConflictError,
    StrategyAuthoringError,
    StrategyBaseStore,
    _atomic_create_immutable,
    analyze_strategy_source,
    base_publication_resolution,
    describe_base_resolution,
    diff_source_documents,
    diff_strategy_resolutions,
    farm_source_from_resolution,
    fingerprint_document,
    legacy_farm_source_to_strategy_source,
    materialize_loadout_preset as materialize_authoring_loadout_preset,
    normalize_base_source,
    normalize_strategy_source,
    preview_strategy_rebase,
    rebase_review_fingerprint,
    resolve_strategy_source,
    setting_registry_catalog,
    upgrade_authoring_source_schema,
)
from tools.strategy_builders.lib import build_strategy_yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILTIN_STRATEGIES_DIR = PROJECT_ROOT / "config" / "strategies"
FARM_RUN_PROFILE_PATH = PROJECT_ROOT / "config" / "run_profiles" / "farm.yaml"
DEFAULT_CUSTOM_STRATEGY_DIR = BUILTIN_STRATEGIES_DIR / "custom"
STRATEGY_PROFILE_DIRECTORY_ENVIRONMENT_VARIABLE = (
    "THETOWER_STRATEGY_PROFILE_DIR"
)
BUILTIN_STRATEGY_IDS = (
    "farm_t18",
    "farm_t19",
    "tournament",
    "none",
)
LEGACY_STRATEGY_ALIASES = {
    "farm": "farm_t18",
    "farm_t19_experiment": "farm_t19",
    "gc": "farm_t18",
    "gc_farm_t18": "farm_t18",
    "gc_farm_t19": "farm_t19",
    "gc_farm_t19_experiment": "farm_t19",
    "gc_skipper": "farm_t18",
    "glass_cannon": "farm_t18",
    "gc_manual_target_priority": "farm_t19",
}
POLICY_MODES = ("enforce", "observe", "preserve")
FARM_LOADOUT_KEYS = (
    "modules",
    "damage_slider",
    "orb_distance",
    "target_priority",
)
STRATEGY_PROFILE_SCHEMA_VERSION = 1
STRATEGY_PUBLICATION_SCHEMA_VERSION = 2
STRATEGY_AUTHORING_API_SCHEMA_VERSION = 1
STRATEGY_AUTHORING_OPERATIONS = (
    "validate_base",
    "publish_base",
    "validate_strategy",
    "publish_strategy",
    "preview_rebase",
    "retire_strategy",
    "compare_strategy_revision",
    "preview_restore_strategy",
    "publish_restore_strategy",
    "materialize_loadout_preset",
    "create_module_preset",
)
MAX_PROFILE_FILE_BYTES = 4 * 1024 * 1024
STRATEGY_REVISION_SCHEMA_VERSION = 1
STRATEGY_HISTORY_API_SCHEMA_VERSION = 1
STRATEGY_TRANSACTION_SCHEMA_VERSION = 1
STRATEGY_HISTORY_DIRECTORY_NAME = "history"
STRATEGY_TRANSACTION_DIRECTORY_NAME = "transactions"
CAPTURED_STRATEGY_DRAFT_SCHEMA_VERSION = 1
CAPTURED_STRATEGY_DRAFT_DIRECTORY_NAME = "captured_drafts"
STRATEGY_PUBLICATION_ORIGINS = frozenset(
    {
        "authoring_publication",
        "profile_facade_publication",
        "conservative_adoption",
        "restore_as_new",
        "clone_from_revision",
    }
)
_AUDIT_AUTHORITY = "thetower_control_surface"
_STRATEGY_ID_RE = re.compile(r"[a-z][a-z0-9_]{2,47}")
_AUDIT_EVENT_ID_RE = re.compile(r"[0-9a-f]{32}")
_REVISION_FILENAME_RE = re.compile(
    r"(?P<id>[a-z][a-z0-9_]{2,47})\.strategy\."
    r"(?P<version>[1-9][0-9]*)\.yaml"
)
_TRANSACTION_FILENAME_RE = re.compile(
    r"(?P<id>[a-z][a-z0-9_]{2,47})\.publication\.yaml"
)
_TRANSACTION_STAGE_FILENAME_RE = re.compile(
    r"\.(?P<id>[a-z][a-z0-9_]{2,47})\."
    r"(?:revision\.stage|latest\.stage|previous)\.yaml"
)
_CAPTURED_DRAFT_FILENAME_RE = re.compile(
    r"(?P<id>[a-z][a-z0-9_]{2,47})\.captured-strategy-draft\.yaml"
)
_RESERVED_STRATEGY_IDS = frozenset(
    {*BUILTIN_STRATEGY_IDS, *LEGACY_STRATEGY_ALIASES}
)
_PROFILE_ID_CACHE: dict[
    Path,
    tuple[tuple[tuple[str, int, int, bool], ...], tuple[str, ...]],
] = {}
_PROFILE_ID_CACHE_LOCK = threading.Lock()


class StrategyProfileError(ValueError):
    """Raised when a profile draft or publication is invalid."""


class StrategyProfileConflictError(StrategyProfileError):
    """Raised when a publication would overwrite a newer profile revision."""


def strategy_profile_directory(
    profile_directory: Path | str | None = None,
) -> Path:
    """Resolve the fixed custom-profile directory used by this process."""

    if profile_directory is not None:
        return Path(profile_directory).expanduser().resolve()
    configured = os.getenv(STRATEGY_PROFILE_DIRECTORY_ENVIRONMENT_VARIABLE)
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_CUSTOM_STRATEGY_DIR.resolve()


def normalize_strategy_id(value: object) -> Optional[str]:
    """Return a safe canonical identifier without resolving aliases."""

    normalized = str(value or "").strip().lower()
    return normalized if _STRATEGY_ID_RE.fullmatch(normalized) else None


def canonical_strategy_id(value: object) -> Optional[str]:
    """Resolve a safe legacy name to its canonical bundled identifier."""

    normalized = normalize_strategy_id(value)
    if normalized is None:
        return None
    return LEGACY_STRATEGY_ALIASES.get(normalized, normalized)


def configurable_strategy_ids(
    profile_directory: Path | str | None = None,
) -> tuple[str, ...]:
    """Return selectable bundled and valid published strategy identifiers."""

    custom = published_custom_strategy_ids(profile_directory)
    return (*BUILTIN_STRATEGY_IDS, *custom)


def is_configurable_strategy(
    value: object,
    profile_directory: Path | str | None = None,
    *,
    allow_legacy_aliases: bool = True,
) -> bool:
    """Report whether a strategy can be loaded by the current installation."""

    normalized = normalize_strategy_id(value)
    if normalized is None:
        return False
    if normalized in BUILTIN_STRATEGY_IDS:
        return True
    if allow_legacy_aliases and normalized in LEGACY_STRATEGY_ALIASES:
        return True
    return normalized in published_custom_strategy_ids(profile_directory)


def published_custom_strategy_ids(
    profile_directory: Path | str | None = None,
) -> tuple[str, ...]:
    """Return custom identifiers whose atomic publication is valid."""

    directory = strategy_profile_directory(profile_directory)
    if not directory.is_dir():
        return ()
    signature_items: list[tuple[str, int, int, bool]] = []
    for path in sorted(directory.glob("*.profile.yaml")):
        try:
            stat_result = path.stat()
        except OSError:
            continue
        signature_items.append(
            (
                path.name,
                stat_result.st_mtime_ns,
                stat_result.st_size,
                path.is_symlink(),
            )
        )
    signature = tuple(signature_items)
    with _PROFILE_ID_CACHE_LOCK:
        cached = _PROFILE_ID_CACHE.get(directory)
        if cached is not None and cached[0] == signature:
            return cached[1]

    identifiers: list[str] = []
    for path in sorted(directory.glob("*.profile.yaml")):
        identifier = path.name.removesuffix(".profile.yaml")
        if normalize_strategy_id(identifier) != identifier or path.is_symlink():
            continue
        try:
            _load_publication(path, expected_id=identifier)
        except StrategyProfileError:
            continue
        identifiers.append(identifier)
    result = tuple(identifiers)
    with _PROFILE_ID_CACHE_LOCK:
        _PROFILE_ID_CACHE[directory] = (signature, result)
    return result


def load_published_strategy_plan(
    strategy_id: object,
    profile_directory: Path | str | None = None,
) -> Optional[dict[str, Any]]:
    """Load the generated plan from one fixed-name custom publication."""

    identifier = normalize_strategy_id(strategy_id)
    if identifier is None or identifier in _RESERVED_STRATEGY_IDS:
        return None
    path = _profile_path(strategy_profile_directory(profile_directory), identifier)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        publication = _load_publication(path, expected_id=identifier)
    except StrategyProfileError:
        return None
    return _copy_mapping(publication["plan"])


class StrategyProfileStore:
    """Build, publish, and enumerate constrained Farm strategy profiles."""

    def __init__(
        self,
        *,
        profile_directory: Path | str | None = None,
        builtin_strategies_directory: Path | str = BUILTIN_STRATEGIES_DIR,
        base_directory: Path | str | None = None,
        module_preset_directory: Path | str | None = None,
        audit_callback: Optional[Callable[[str], object]] = None,
        transaction_fault_hook: Optional[Callable[[str], None]] = None,
    ) -> None:
        profile_directory_was_explicit = profile_directory is not None
        self.profile_directory = strategy_profile_directory(profile_directory)
        self.builtin_strategies_directory = Path(
            builtin_strategies_directory
        ).resolve()
        self.module_preset_store = ModulePresetStore(
            module_preset_directory
            if module_preset_directory is not None
            else (
                self.profile_directory.parent / "module_presets"
                if profile_directory_was_explicit
                else DEFAULT_CUSTOM_MODULE_PRESET_DIRECTORY
            )
        )
        self.base_store = StrategyBaseStore(
            base_directory or self.profile_directory / "bases",
            module_preset_definitions_factory=self._module_preset_definitions,
        )
        self._audit_callback = audit_callback
        self._transaction_fault_hook = transaction_fault_hook
        self._publish_lock = threading.RLock()
        self._reported_history_events: set[str] = set()

    def strategy_ids(self) -> tuple[str, ...]:
        return configurable_strategy_ids(self.profile_directory)

    def has_strategy(self, value: object) -> bool:
        return is_configurable_strategy(
            value,
            self.profile_directory,
            allow_legacy_aliases=False,
        )

    def setting_registry(
        self,
        module_preset_catalog: Optional[Mapping[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """Return backend authoring metadata without exposing normalizers."""

        selected_catalog = (
            self.module_preset_store.catalog()
            if module_preset_catalog is None
            else module_preset_catalog
        )
        return setting_registry_catalog(
            module_preset_catalog=selected_catalog,
        )

    def create_module_preset(
        self,
        preset_id: object,
        display_name: object,
        source: object,
    ) -> dict[str, Any]:
        """Create one immutable custom Module preset from a preset or local value."""

        if not isinstance(source, Mapping):
            raise ModulePresetError(
                "Module preset source must select exactly one preset or local definition",
                code="invalid_module_preset_source",
                field="source",
            )
        if set(source) == {"preset"}:
            definition = self.module_preset_store.definition(source.get("preset"))
        elif set(source) == {"local"}:
            definition = source.get("local")
        else:
            raise ModulePresetError(
                "Module preset source must define exactly one of preset or local",
                code="invalid_module_preset_source",
                field="source",
            )
        return self.module_preset_store.create(
            preset_id,
            display_name,
            definition,
        )

    def materialize_loadout_preset(
        self,
        setting_id: object,
        preset_id: object,
        expected_catalog_fingerprint: object,
    ) -> dict[str, Any]:
        """Resolve one displayed preset into an exact normalized local draft."""

        module_catalog = self.module_preset_store.catalog()
        try:
            return materialize_authoring_loadout_preset(
                setting_id,
                preset_id,
                expected_catalog_fingerprint,
                module_preset_definitions=module_preset_definitions(
                    module_catalog
                ),
            )
        except StrategyAuthoringConflictError as exc:
            raise StrategyProfileConflictError(str(exc)) from exc
        except StrategyAuthoringError as exc:
            raise StrategyProfileError(str(exc)) from exc

    def review_captured_strategy_draft(
        self,
        capture: object,
        *,
        strategy_id: object,
        display_name: object,
        tier: object,
        base: object = None,
        expected_capture_fingerprint: object = None,
    ) -> dict[str, Any]:
        """Return a fingerprinted capture-versus-Base review without writing."""

        safe_capture = validate_setup_capture_preview(capture)
        if safe_capture is None:
            raise StrategyProfileError(
                "Capture preview lacks exact runtime-issued forced-save evidence"
            )
        capture_fingerprint = fingerprint_document(safe_capture)
        expected = str(expected_capture_fingerprint or "").strip()
        if not expected:
            raise StrategyProfileConflictError(
                "Exact reviewed capture fingerprint is required"
            )
        if expected != capture_fingerprint:
            raise StrategyProfileConflictError(
                "Capture preview changed after review; refresh it before saving"
            )
        try:
            proposed = strategy_source_from_capture(
                safe_capture,
                strategy_id=strategy_id,
                display_name=display_name,
                tier=tier,
                base=base,
            )
        except SetupCaptureError as exc:
            raise StrategyProfileError(str(exc)) from exc
        review_base = _copy_optional_mapping(proposed.get("base"))
        # A captured draft must not make unresolved save fields appear to be
        # captured merely because an optional comparison Base has values for
        # them.  Retain that Base as review context; pinning it remains an
        # explicit ordinary authoring decision after unresolved rows are read.
        source = _copy_mapping(proposed)
        source.pop("base", None)
        identifier = source["id"]
        if identifier in _RESERVED_STRATEGY_IDS:
            raise StrategyProfileError(
                f"{identifier!r} is a bundled or reserved strategy name"
            )
        validation = self.validate_authoring_strategy(source)
        source = _copy_mapping(validation["source"])
        if isinstance(review_base, Mapping):
            publication = self.base_store.load(
                review_base.get("id"), review_base.get("revision")
            )
            captured_vs_base = _diff_captured_strategy_resolutions(
                base_publication_resolution(publication),
                validation["resolution"],
            )
        else:
            captured_vs_base = _copy_mapping(
                validation["review"]["effective_changes"]
            )
        review: dict[str, Any] = {
            "schema_version": CAPTURED_STRATEGY_DRAFT_SCHEMA_VERSION,
            "kind": "captured_strategy_draft_review",
            "capture_fingerprint": capture_fingerprint,
            "source": source,
            "source_fingerprint": validation["fingerprints"][
                "source_fingerprint"
            ],
            "resolution": _copy_mapping(validation["resolution"]),
            "captured_vs_base": {
                "base": _copy_optional_mapping(review_base),
                **captured_vs_base,
            },
            "unresolved": _copy_sequence_of_mappings(
                safe_capture.get("unresolved")
            ),
            "validation": _copy_mapping(validation["review"]),
            "fingerprints": _copy_mapping(validation["fingerprints"]),
            "saving_activates_strategy": False,
            "publication_activates_strategy": False,
        }
        review["review_fingerprint"] = fingerprint_document(review)
        return review

    def save_captured_strategy_draft(
        self,
        capture: object,
        *,
        strategy_id: object,
        display_name: object,
        tier: object,
        base: object = None,
        expected_capture_fingerprint: object = None,
        expected_review_fingerprint: object = None,
    ) -> dict[str, Any]:
        """Atomically save one reviewed capture without publishing it."""

        review = self.review_captured_strategy_draft(
            capture,
            strategy_id=strategy_id,
            display_name=display_name,
            tier=tier,
            base=base,
            expected_capture_fingerprint=expected_capture_fingerprint,
        )
        reviewed_fingerprint = str(expected_review_fingerprint or "").strip()
        if not reviewed_fingerprint:
            raise StrategyProfileConflictError(
                "Exact captured-versus-Base review fingerprint is required"
            )
        if reviewed_fingerprint != review["review_fingerprint"]:
            raise StrategyProfileConflictError(
                "Captured-versus-Base review changed; review it again before saving"
            )
        safe_capture = _copy_mapping(capture)
        source = _copy_mapping(review["source"])
        identifier = source["id"]
        capture_fingerprint = str(review["capture_fingerprint"])
        saved_at = datetime.now().astimezone().isoformat(timespec="seconds")
        payload: dict[str, Any] = {
            "schema_version": CAPTURED_STRATEGY_DRAFT_SCHEMA_VERSION,
            "kind": "captured_strategy_draft",
            "id": identifier,
            "saved_at": saved_at,
            "source": source,
            "source_fingerprint": review["source_fingerprint"],
            "capture": safe_capture,
            "capture_fingerprint": capture_fingerprint,
            "review": {
                "captured_vs_base": _copy_mapping(
                    review["captured_vs_base"]
                ),
                "unresolved": _copy_sequence_of_mappings(review["unresolved"]),
                "validation": _copy_mapping(review["validation"]),
                "review_fingerprint": review["review_fingerprint"],
                "saving_activates_strategy": False,
                "publication_activates_strategy": False,
            },
            "fingerprints": _copy_mapping(review["fingerprints"]),
        }
        payload["draft_fingerprint"] = fingerprint_document(payload)

        with self._publish_lock:
            with self._catalog_write_lock():
                if _profile_path(self.profile_directory, identifier).exists():
                    raise StrategyProfileConflictError(
                        f"Strategy {identifier!r} is already published; choose a new draft ID"
                    )
                directory = self._captured_draft_directory()
                self._prepare_captured_draft_directory(directory)
                path = self._captured_draft_path(identifier)
                if path.exists() or path.is_symlink():
                    raise StrategyProfileConflictError(
                        f"Captured Strategy draft {identifier!r} already exists"
                    )
                try:
                    _atomic_create_immutable(
                        directory,
                        path,
                        payload,
                        description="captured Strategy draft",
                    )
                except StrategyAuthoringConflictError as exc:
                    raise StrategyProfileConflictError(str(exc)) from exc
                except StrategyAuthoringError as exc:
                    raise StrategyProfileError(str(exc)) from exc
                return self._load_captured_strategy_draft(path, identifier)

    def captured_strategy_draft(
        self,
        strategy_id: object,
    ) -> dict[str, Any]:
        """Load one immutable captured draft without publishing or selecting it."""

        identifier = _draft_identifier({"id": strategy_id})
        if identifier in _RESERVED_STRATEGY_IDS:
            raise StrategyProfileError(
                f"{identifier!r} is a bundled or reserved strategy name"
            )
        return self._load_captured_strategy_draft(
            self._captured_draft_path(identifier),
            identifier,
        )

    def captured_strategy_draft_catalog(self) -> dict[str, Any]:
        """Return path-free summaries for immutable captured drafts."""

        directory = self._captured_draft_directory()
        if not directory.exists():
            return {
                "schema_version": CAPTURED_STRATEGY_DRAFT_SCHEMA_VERSION,
                "items": [],
                "errors": [],
            }
        if directory.is_symlink() or not directory.is_dir():
            return {
                "schema_version": CAPTURED_STRATEGY_DRAFT_SCHEMA_VERSION,
                "items": [],
                "errors": [
                    {
                        "id": "captured_drafts",
                        "error": "Captured Strategy draft directory is invalid",
                    }
                ],
            }
        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for path in sorted(directory.glob("*.captured-strategy-draft.yaml")):
            match = _CAPTURED_DRAFT_FILENAME_RE.fullmatch(path.name)
            if match is None:
                errors.append(
                    {
                        "id": "invalid_filename",
                        "error": (
                            "Captured Strategy draft filename is invalid: "
                            f"{path.name}"
                        ),
                    }
                )
                continue
            identifier = match.group("id")
            try:
                draft = self._load_captured_strategy_draft(path, identifier)
                source = draft["source"]
                items.append(
                    {
                        "id": identifier,
                        "display_name": source["display_name"],
                        "tier": source["tier"],
                        "saved_at": draft["saved_at"],
                        "draft_fingerprint": draft["draft_fingerprint"],
                        "capture_fingerprint": draft["capture_fingerprint"],
                        "acquisition_source": draft["capture"][
                            "capture_origin"
                        ]["acquisition_source"],
                        "unresolved_count": len(
                            draft["review"]["unresolved"]
                        ),
                        "published": False,
                        "selected": False,
                        "queued": False,
                    }
                )
            except StrategyProfileError as exc:
                errors.append({"id": identifier, "error": str(exc)})
        return {
            "schema_version": CAPTURED_STRATEGY_DRAFT_SCHEMA_VERSION,
            "items": items,
            "errors": errors,
        }

    def _captured_draft_directory(self) -> Path:
        return self.profile_directory / CAPTURED_STRATEGY_DRAFT_DIRECTORY_NAME

    def _captured_draft_path(self, identifier: str) -> Path:
        return self._captured_draft_directory() / (
            f"{identifier}.captured-strategy-draft.yaml"
        )

    @staticmethod
    def _prepare_captured_draft_directory(directory: Path) -> None:
        try:
            if directory.is_symlink() or (
                directory.exists() and not directory.is_dir()
            ):
                raise StrategyProfileConflictError(
                    "Captured Strategy draft directory is not a regular directory"
                )
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        except StrategyProfileError:
            raise
        except OSError as exc:
            raise StrategyProfileError(
                f"Unable to prepare captured Strategy draft directory: {exc}"
            ) from exc

    @staticmethod
    def _load_captured_strategy_draft(
        path: Path,
        expected_id: str,
    ) -> dict[str, Any]:
        raw = _load_yaml_mapping_limited_profile(
            path,
            f"captured Strategy draft {expected_id!r}",
        )
        if (
            set(raw)
            != {
                "schema_version",
                "kind",
                "id",
                "saved_at",
                "source",
                "source_fingerprint",
                "capture",
                "capture_fingerprint",
                "review",
                "fingerprints",
                "draft_fingerprint",
            }
            or
            raw.get("schema_version")
            != CAPTURED_STRATEGY_DRAFT_SCHEMA_VERSION
            or raw.get("kind") != "captured_strategy_draft"
            or raw.get("id") != expected_id
        ):
            raise StrategyProfileError(
                f"Captured Strategy draft {expected_id!r} has invalid identity"
            )
        try:
            source = normalize_strategy_source(raw.get("source"))
        except StrategyAuthoringError as exc:
            raise StrategyProfileError(str(exc)) from exc
        if source.get("id") != expected_id or source != raw.get("source"):
            raise StrategyProfileError(
                f"Captured Strategy draft {expected_id!r} has noncanonical source"
            )
        capture = raw.get("capture")
        review = raw.get("review")
        fingerprints = raw.get("fingerprints")
        try:
            saved_at = datetime.fromisoformat(str(raw.get("saved_at") or ""))
        except ValueError:
            saved_at = None
        if not all(
            isinstance(value, Mapping)
            for value in (capture, review, fingerprints)
        ) or saved_at is None or saved_at.tzinfo is None:
            raise StrategyProfileError(
                f"Captured Strategy draft {expected_id!r} is incomplete"
            )
        normalized_capture = validate_setup_capture_preview(capture)
        if normalized_capture is None or normalized_capture != capture:
            raise StrategyProfileError(
                f"Captured Strategy draft {expected_id!r} has invalid save evidence"
            )
        if (
            set(review)
            != {
                "captured_vs_base",
                "unresolved",
                "validation",
                "review_fingerprint",
                "saving_activates_strategy",
                "publication_activates_strategy",
            }
            or not isinstance(review.get("captured_vs_base"), Mapping)
            or not isinstance(review.get("unresolved"), list)
            or not isinstance(review.get("validation"), Mapping)
            or review.get("unresolved") != normalized_capture["unresolved"]
            or review.get("saving_activates_strategy") is not False
            or review.get("publication_activates_strategy") is not False
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                str(review.get("review_fingerprint") or ""),
            )
        ):
            raise StrategyProfileError(
                f"Captured Strategy draft {expected_id!r} has invalid review evidence"
            )
        if raw.get("source_fingerprint") != fingerprint_document(source):
            raise StrategyProfileError(
                f"Captured Strategy draft {expected_id!r} source fingerprint disagrees"
            )
        if raw.get("capture_fingerprint") != fingerprint_document(capture):
            raise StrategyProfileError(
                f"Captured Strategy draft {expected_id!r} capture fingerprint disagrees"
            )
        draft_fingerprint = str(raw.get("draft_fingerprint") or "")
        unsigned = dict(raw)
        unsigned.pop("draft_fingerprint", None)
        if draft_fingerprint != fingerprint_document(unsigned):
            raise StrategyProfileError(
                f"Captured Strategy draft {expected_id!r} fingerprint disagrees"
            )
        return _copy_mapping(raw)

    def _module_preset_definitions(self) -> dict[str, dict[str, str]]:
        return module_preset_definitions(self.module_preset_store.catalog())

    def authoring_catalog(self) -> dict[str, Any]:
        """Return the additive Base/Strategy editor contract."""

        module_preset_catalog = self.module_preset_store.catalog()
        history_errors = self._prepare_history()
        base_catalog = self.base_store.catalog()
        captured_drafts = self.captured_strategy_draft_catalog()
        legacy_catalog = self.catalog(
            _history_prepared=True,
            _module_preset_catalog=module_preset_catalog,
        )
        strategy_items: list[dict[str, Any]] = []
        strategy_errors = [
            {"id": item["id"], "error": item["error"]}
            for item in legacy_catalog["errors"]
        ]
        for summary in legacy_catalog["items"]:
            try:
                strategy_items.append(
                    self._authoring_catalog_item(summary, base_catalog["items"])
                )
            except StrategyProfileError as exc:
                strategy_errors.append(
                    {"id": str(summary.get("id") or "unknown"), "error": str(exc)}
                )

        catalog_errors = [
            {"catalog": "bases", **error} for error in base_catalog["errors"]
        ] + [
            {"catalog": "strategies", **error} for error in strategy_errors
        ] + [
            {"catalog": "strategy_history", **error}
            for error in history_errors
        ] + [
            {"catalog": "module_presets", **error}
            for error in module_preset_catalog["errors"]
        ] + [
            {"catalog": "captured_drafts", **error}
            for error in captured_drafts["errors"]
        ]
        return {
            "schema_version": STRATEGY_AUTHORING_API_SCHEMA_VERSION,
            "setting_registry": self.setting_registry(module_preset_catalog),
            "capabilities": {
                "operations": list(STRATEGY_AUTHORING_OPERATIONS),
                "base_source_states": [
                    {
                        "id": "not_included",
                        "display_name": "Not Included",
                        "policy": None,
                    },
                    {
                        "id": "included_enforce",
                        "display_name": "Included Enforce",
                        "policy": "enforce",
                    },
                    {
                        "id": "included_observe",
                        "display_name": "Included Observe",
                        "policy": "observe",
                    },
                ],
                "strategy_source_states": [
                    {
                        "id": "inherit",
                        "display_name": "Inherit",
                        "policy": None,
                    },
                    {
                        "id": "override_enforce",
                        "display_name": "Override Enforce",
                        "policy": "enforce",
                    },
                    {
                        "id": "override_observe",
                        "display_name": "Override Observe",
                        "policy": "observe",
                    },
                    {
                        "id": "ignore",
                        "display_name": "Ignore",
                        "policy": "ignore",
                    },
                ],
                "publication_activates_strategy": False,
                "expanded_plan_exposed": False,
                "unknown_values_round_trip": True,
                "reviewed_rebase_required": True,
                "immutable_strategy_history": True,
                "restore_as_new": True,
                "profile_local_loadout_editors": True,
                "preset_local_copy": True,
                "managed_custom_module_presets": True,
                "save_backed_setup_capture": True,
            },
            "editor_options": {
                "presets": legacy_catalog["presets"],
                "perks": legacy_catalog["perks"],
            },
            "bases": base_catalog,
            "strategies": {
                "items": strategy_items,
                "errors": strategy_errors,
            },
            "latest_compatible_base_revisions": [
                {
                    "id": item["id"],
                    "display_name": item["display_name"],
                    "family": item["family"],
                    "revision": item["latest_revision"],
                    "source_fingerprint": item["source_fingerprint"],
                }
                for item in base_catalog["items"]
            ],
            "module_presets": module_preset_catalog,
            "captured_drafts": captured_drafts,
            "catalog_errors": catalog_errors,
        }

    def history_catalog(
        self,
        strategy_id: object = None,
    ) -> dict[str, Any]:
        """Return immutable custom-Strategy lineage summaries newest first."""

        identifier: Optional[str] = None
        if strategy_id is not None:
            identifier = _draft_identifier({"id": strategy_id})
            if identifier in _RESERVED_STRATEGY_IDS:
                raise StrategyProfileError(
                    f"{identifier!r} is a bundled or reserved strategy name"
                )
        with self._publish_lock:
            with self._catalog_write_lock():
                errors = self._prepare_history_under_file_lock()
                return self._history_catalog_under_file_lock(
                    identifier=identifier,
                    preparation_errors=errors,
                )

    def history_revision(
        self,
        strategy_id: object,
        logical_version: object,
    ) -> dict[str, Any]:
        """Return one retained revision without exposing its generated plan."""

        identifier = _draft_identifier({"id": strategy_id})
        version = _positive_version(logical_version, "logical_version")
        if identifier in _RESERVED_STRATEGY_IDS:
            raise StrategyProfileError(
                f"{identifier!r} is a bundled or reserved strategy name"
            )
        with self._publish_lock:
            with self._catalog_write_lock():
                errors = self._prepare_history_under_file_lock()
                self._raise_lineage_history_error(identifier, errors)
                revision = self._load_revision(identifier, version)
                latest = self._latest_publication(identifier)
                summary = self._revision_summary(
                    revision,
                    latest=latest,
                    lineage_retired=latest is None,
                    lineage_latest_version=max(
                        self._revision_versions(identifier),
                        default=version,
                    ),
                )
                return {
                    "schema_version": STRATEGY_HISTORY_API_SCHEMA_VERSION,
                    "revision": summary,
                    "source": _copy_mapping(revision["source"]),
                    "base_snapshot": _copy_optional_mapping(
                        revision.get("base_snapshot")
                    ),
                    "base_resolution": _copy_optional_mapping(
                        revision.get("base_resolution")
                    ),
                    "resolution": _copy_mapping(revision["resolution"]),
                    "expanded_plan_exposed": False,
                }

    def compare_strategy_revision(
        self,
        strategy_id: object,
        logical_version: object,
        *,
        expected_revision_fingerprint: object = None,
        expected_latest_source_fingerprint: object = None,
        require_optimistic_state: bool = False,
    ) -> dict[str, Any]:
        """Compare a retained intent with the current latest publication."""

        identifier = _draft_identifier({"id": strategy_id})
        version = _positive_version(logical_version, "logical_version")
        if identifier in _RESERVED_STRATEGY_IDS:
            raise StrategyProfileError(
                f"{identifier!r} is a bundled or reserved strategy name"
            )
        with self._publish_lock:
            with self._catalog_write_lock():
                errors = self._prepare_history_under_file_lock()
                self._raise_lineage_history_error(identifier, errors)
                response = self._restore_preview_under_file_lock(
                    identifier,
                    version,
                    expected_revision_fingerprint=(
                        expected_revision_fingerprint
                    ),
                    expected_latest_source_fingerprint=(
                        expected_latest_source_fingerprint
                    ),
                    require_optimistic_state=require_optimistic_state,
                )
                response.pop("_candidate", None)
                return response

    def publish_restore_strategy(
        self,
        strategy_id: object,
        logical_version: object,
        *,
        expected_revision_fingerprint: object,
        expected_latest_source_fingerprint: object,
        reviewed_restore_fingerprint: object,
    ) -> dict[str, Any]:
        """Publish historical intent as the next revision of the same lineage."""

        identifier = _draft_identifier({"id": strategy_id})
        version = _positive_version(logical_version, "logical_version")
        if identifier in _RESERVED_STRATEGY_IDS:
            raise StrategyProfileError(
                f"{identifier!r} is a bundled or reserved strategy name"
            )
        with self._publish_lock:
            with self._catalog_write_lock():
                errors = self._prepare_history_under_file_lock()
                self._raise_lineage_history_error(identifier, errors)
                preview = self._restore_preview_under_file_lock(
                    identifier,
                    version,
                    expected_revision_fingerprint=(
                        expected_revision_fingerprint
                    ),
                    expected_latest_source_fingerprint=(
                        expected_latest_source_fingerprint
                    ),
                    require_optimistic_state=True,
                )
                expected_review = preview["reviewed_restore_fingerprint"]
                supplied_review = str(reviewed_restore_fingerprint or "").strip()
                if supplied_review != expected_review:
                    raise StrategyProfileConflictError(
                        "The reviewed restore comparison is stale; review the "
                        "historical revision again before publishing"
                    )
                candidate = preview["_candidate"]
                result = self._publish_validated_under_file_lock(
                    candidate["validation"],
                    expected_source_fingerprint=(
                        preview["current_latest_source_fingerprint"]
                    ),
                    origin="restore_as_new",
                    allow_retired_lineage=True,
                )
                result["restored"] = True
                result["restored_from"] = {
                    "id": identifier,
                    "logical_version": version,
                    "revision_fingerprint": preview[
                        "historical_revision_fingerprint"
                    ],
                }
                result["comparison"] = preview["comparison"]
                result["reviewed_restore_fingerprint"] = expected_review
                result["source"] = _copy_mapping(
                    candidate["validation"]["source"]
                )
                result["resolution"] = _copy_mapping(
                    candidate["validation"]["resolution"]
                )
                return result

    def validate_base(self, raw_base: object) -> dict[str, Any]:
        """Normalize a prospective next immutable Base revision."""

        try:
            upgraded = upgrade_authoring_source_schema(raw_base)
            initial = normalize_base_source(upgraded, revision=1)
            latest = self.base_store.latest(initial["id"])
            revision = (
                int(latest["snapshot"]["revision"]) + 1
                if latest is not None
                else 1
            )
            source = normalize_base_source(upgraded, revision=revision)
        except StrategyAuthoringError as exc:
            raise StrategyProfileError(str(exc)) from exc
        before = (
            _copy_mapping(latest["snapshot"])
            if latest is not None
            else {
                **_copy_mapping(source),
                "settings": {},
            }
        )
        review = diff_source_documents(before, source)
        review["created"] = latest is None
        resolution = describe_base_resolution(
            source,
            module_preset_definitions=self._module_preset_definitions(),
        )
        source_fingerprint = fingerprint_document(source)
        resolution_fingerprint = fingerprint_document(resolution)
        return {
            "valid": True,
            "published": False,
            "source": source,
            "resolution": resolution,
            "source_fingerprint": source_fingerprint,
            "expected_latest_fingerprint": (
                latest["source_fingerprint"] if latest is not None else None
            ),
            "fingerprints": {
                "source_fingerprint": source_fingerprint,
                "resolution_fingerprint": resolution_fingerprint,
            },
            "review": {
                "source_changes": review,
                "validation": {"valid": True, "errors": []},
                "publication_activates_strategy": False,
            },
            "summary": [
                f"Base {source['display_name']} revision {source['revision']}",
                f"{len(source['settings'])} included setting(s)",
                "Publishing creates a new immutable revision.",
            ],
        }

    def publish_authoring_base(
        self,
        raw_base: object,
        *,
        expected_latest_fingerprint: object = None,
    ) -> dict[str, Any]:
        """Validate and atomically append one Base revision."""

        validation = self.validate_base(raw_base)
        publication = self.publish_base(
            raw_base,
            expected_latest_fingerprint=expected_latest_fingerprint,
        )
        validation.update(
            {
                "published": True,
                "source": _copy_mapping(publication["snapshot"]),
                "resolution": base_publication_resolution(publication),
                "source_fingerprint": publication["source_fingerprint"],
                "published_at": publication["published_at"],
                "fingerprints": {
                    "source_fingerprint": publication["source_fingerprint"],
                    "resolution_fingerprint": publication.get(
                        "resolution_fingerprint"
                    ),
                },
            }
        )
        return validation

    def validate_authoring_strategy(
        self,
        raw_strategy: object,
    ) -> dict[str, Any]:
        """Validate sparse Strategy source and return only authoring output."""

        validation = self.validate(raw_strategy)
        source = validation["source"]
        resolution = validation["resolution"]
        current = self._existing_authoring_state(source["id"])
        if current is None:
            before_source = {
                **_copy_mapping(source),
                "base": None,
                "settings": {},
            }
            before_source.pop("base", None)
            before_resolution = analyze_strategy_source(
                before_source,
                module_preset_definitions=self._module_preset_definitions(),
            )["resolution"]
        else:
            before_source = current["source"]
            before_resolution = current["resolution"]
        source_changes = diff_source_documents(before_source, source)
        source_changes["created"] = current is None
        effective_changes = diff_strategy_resolutions(
            before_resolution,
            resolution,
        )
        profile = _copy_mapping(validation["profile"])
        return {
            "valid": True,
            "published": False,
            "profile": profile,
            "source": _copy_mapping(source),
            "resolution": _copy_mapping(resolution),
            "resolved_configuration": _copy_mapping(
                validation["resolved_configuration"]
            ),
            "rule_count": validation["rule_count"],
            "summary": list(validation["summary"]),
            "fingerprints": {
                "source_fingerprint": profile["source_fingerprint"],
                "base_fingerprint": profile["base_fingerprint"],
                "resolution_fingerprint": profile["resolution_fingerprint"],
                "plan_fingerprint": profile["plan_fingerprint"],
            },
            "review": {
                "source_changes": source_changes,
                "effective_changes": effective_changes,
                "validation": {"valid": True, "errors": []},
                "rule_count": validation["rule_count"],
                "fingerprints": {
                    "source_fingerprint": profile["source_fingerprint"],
                    "base_fingerprint": profile["base_fingerprint"],
                    "resolution_fingerprint": profile[
                        "resolution_fingerprint"
                    ],
                    "plan_fingerprint": profile["plan_fingerprint"],
                },
                "publication_activates_strategy": False,
            },
        }

    def publish_authoring_strategy(
        self,
        raw_strategy: object,
        *,
        expected_source_fingerprint: object = None,
        reviewed_rebase_fingerprint: object = None,
    ) -> dict[str, Any]:
        """Publish sparse Strategy source after any pin change was reviewed."""

        identifier = _draft_identifier(raw_strategy)
        if identifier in _RESERVED_STRATEGY_IDS:
            raise StrategyProfileError(
                f"{identifier!r} is a bundled or reserved strategy name; "
                "clone it under a new id"
            )
        current = self._existing_authoring_state(identifier)
        expected_publication = (
            str(expected_source_fingerprint or "").strip() or None
        )
        if current is not None:
            if expected_publication != current["source_fingerprint"]:
                raise StrategyProfileConflictError(
                    f"Profile {identifier!r} changed after it was opened; "
                    "reload it before publishing"
                )
        elif expected_publication is not None:
            raise StrategyProfileConflictError(
                f"Profile {identifier!r} no longer exists; reload the catalog"
            )
        try:
            proposed = normalize_strategy_source(
                upgrade_authoring_source_schema(raw_strategy)
            )
        except StrategyAuthoringError as exc:
            raise StrategyProfileError(str(exc)) from exc
        if current is not None and current["source"].get("base") != proposed.get(
            "base"
        ):
            expected_review = rebase_review_fingerprint(proposed)
            supplied_review = str(reviewed_rebase_fingerprint or "").strip()
            if supplied_review != expected_review:
                raise StrategyProfileError(
                    "Changing a published Strategy's pinned Base requires a "
                    "fresh reviewed rebase preview"
                )

        validation = self.validate_authoring_strategy(raw_strategy)
        published = self.publish(
            raw_strategy,
            expected_source_fingerprint=expected_source_fingerprint,
            origin="authoring_publication",
        )
        validation["published"] = True
        validation["profile"] = _copy_mapping(published["profile"])
        return validation

    def preview_rebase(
        self,
        raw_strategy: object,
        target_base: object,
    ) -> dict[str, Any]:
        """Preview a Base pin update, including builder/dependency failures."""

        try:
            source = normalize_strategy_source(
                upgrade_authoring_source_schema(raw_strategy)
            )
            current_snapshot, current_base_resolution = (
                self._base_evidence_for_source(source)
            )
            if not isinstance(target_base, Mapping):
                raise StrategyAuthoringError("target_base must be an object")
            target_id = target_base.get("id")
            target_revision = target_base.get("revision")
            if target_revision is None:
                latest = self.base_store.latest(target_id)
                if latest is None:
                    raise StrategyAuthoringError(
                        f"base {target_id!r} has no available revisions"
                    )
                target_publication = latest
            else:
                target_publication = self.base_store.load(
                    target_id,
                    target_revision,
                )
            preview = preview_strategy_rebase(
                source,
                current_snapshot,
                target_publication["snapshot"],
                current_base_resolution_snapshot=current_base_resolution,
                target_base_resolution_snapshot=base_publication_resolution(
                    target_publication
                ),
                module_preset_definitions=self._module_preset_definitions(),
            )
        except StrategyAuthoringError as exc:
            raise StrategyProfileError(str(exc)) from exc

        errors = list(preview["validation_errors"])
        validated: Optional[dict[str, Any]] = None
        try:
            validated = self.validate_authoring_strategy(preview["source"])
        except StrategyProfileError as exc:
            message = str(exc)
            if not any(error.get("message") == message for error in errors):
                errors.append(
                    {
                        "code": "strategy_validation",
                        "message": message,
                    }
                )
        preview["validation_errors"] = errors
        preview["summary"]["validation_error_count"] = len(errors)
        response: dict[str, Any] = {
            "valid": not errors,
            "published": False,
            "source": _copy_mapping(preview["source"]),
            "resolution": _copy_mapping(preview["resolution"]),
            "rebase": preview,
            "reviewed_rebase_fingerprint": preview["review_fingerprint"],
            "review": {
                "validation": {"valid": not errors, "errors": errors},
                "publication_activates_strategy": False,
            },
        }
        if validated is not None:
            response.update(
                {
                    "source": validated["source"],
                    "resolution": validated["resolution"],
                    "profile": validated["profile"],
                    "rule_count": validated["rule_count"],
                    "fingerprints": validated["fingerprints"],
                    "summary": validated["summary"],
                }
            )
            response["rebase"]["source"] = validated["source"]
            response["rebase"]["resolution"] = validated["resolution"]
        return response

    def publish_base(
        self,
        raw_base: object,
        *,
        expected_latest_fingerprint: object = None,
    ) -> dict[str, Any]:
        """Publish the next immutable sparse base revision."""

        try:
            return self.base_store.publish(
                raw_base,
                expected_latest_fingerprint=expected_latest_fingerprint,
            )
        except StrategyAuthoringConflictError as exc:
            raise StrategyProfileConflictError(str(exc)) from exc
        except StrategyAuthoringError as exc:
            raise StrategyProfileError(str(exc)) from exc

    def authoring_source(self, strategy_id: object) -> Optional[dict[str, Any]]:
        """Return a schema-2 source, converting schema-1 only in memory."""

        identifier = normalize_strategy_id(strategy_id)
        if identifier is None:
            return None
        if identifier in {"farm_t18", "farm_t19"}:
            source = _load_yaml_mapping(
                self.builtin_strategies_directory / f"{identifier}.source.yaml",
                "bundled strategy source",
            )
            return legacy_farm_source_to_strategy_source(
                source,
                display_name=_default_display_name(identifier),
            )
        if identifier in _RESERVED_STRATEGY_IDS:
            return None
        path = _profile_path(self.profile_directory, identifier)
        if not path.is_file() or path.is_symlink():
            return None
        publication = _load_publication(path, expected_id=identifier)
        if publication["schema_version"] == STRATEGY_PUBLICATION_SCHEMA_VERSION:
            return _copy_mapping(publication["source"])
        return legacy_farm_source_to_strategy_source(
            publication["source"],
            display_name=publication["display_name"],
        )

    def catalog(
        self,
        *,
        _history_prepared: bool = False,
        _module_preset_catalog: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Return editor metadata without exposing expanded runtime plans."""

        history_errors = (
            [] if _history_prepared else self._prepare_history()
        )
        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = [
            {"id": item["id"], "error": item["error"]}
            for item in history_errors
        ]
        for identifier in BUILTIN_STRATEGY_IDS:
            try:
                items.append(self._builtin_item(identifier))
            except StrategyProfileError as exc:
                errors.append({"id": identifier, "error": str(exc)})

        if self.profile_directory.is_dir():
            for path in sorted(self.profile_directory.glob("*.profile.yaml")):
                identifier = path.name.removesuffix(".profile.yaml")
                try:
                    if path.is_symlink():
                        raise StrategyProfileError(
                            "symbolic-link publications are not supported"
                        )
                    publication = _load_publication(
                        path,
                        expected_id=identifier,
                    )
                    items.append(self._publication_item(publication))
                except StrategyProfileError as exc:
                    errors.append({"id": identifier, "error": str(exc)})

        return {
            "schema_version": STRATEGY_PROFILE_SCHEMA_VERSION,
            "policy_modes": list(POLICY_MODES),
            "presets": self._preset_catalogs(
                _module_preset_catalog
                if _module_preset_catalog is not None
                else self.module_preset_store.catalog()
            ),
            "setup_checks": [
                {
                    "id": check_id,
                    "display_name": STARTUP_GATE_CHECK_LABELS[check_id],
                }
                for check_id in PROFILE_SKIPPABLE_CHECKS
            ],
            "perks": [
                {"id": identifier, "display_name": display_name}
                for identifier, display_name in PERK_CONFIGURATION_LABELS.items()
            ],
            "items": items,
            "errors": errors,
        }

    def validate(self, raw_profile: object) -> dict[str, Any]:
        """Validate a GUI draft and return its resolved, generated summary."""

        source, display_name = self._authoring_source_from_draft(raw_profile)
        try:
            base_snapshot, base_resolution = self._load_base_evidence(source)
            return self._validate_normalized_source(
                source,
                base_snapshot=base_snapshot,
                base_resolution=base_resolution,
                display_name=display_name,
            )
        except StrategyProfileError:
            raise
        except Exception as exc:
            raise StrategyProfileError(str(exc)) from exc

    def _validate_normalized_source(
        self,
        raw_source: object,
        *,
        base_snapshot: object,
        base_resolution: object = None,
        retained_resolution: object = None,
        display_name: object = None,
    ) -> dict[str, Any]:
        """Build one normalized source against an exact embedded Base snapshot."""

        try:
            source = normalize_strategy_source(raw_source)
            resolution = resolve_strategy_source(
                source,
                base_snapshot,
                base_resolution_snapshot=base_resolution,
                retained_resolution=retained_resolution,
                require_base_definition_snapshots=(
                    source["schema_version"] == AUTHORING_SCHEMA_VERSION
                    and base_snapshot is not None
                ),
                module_preset_definitions=self._module_preset_definitions(),
            )
            compact_source = farm_source_from_resolution(source, resolution)
            plan = build_strategy_yaml(compact_source)
        except Exception as exc:
            raise StrategyProfileError(str(exc)) from exc
        normalized_display_name = str(display_name or source["display_name"]).strip()
        if normalized_display_name != source["display_name"]:
            raise StrategyProfileError(
                "Strategy display name does not match its normalized source"
            )
        source_fingerprint = fingerprint_document(source)
        base_fingerprint = _base_state_fingerprint(
            base_snapshot,
            base_resolution,
            source_schema_version=source["schema_version"],
        )
        resolution_fingerprint = fingerprint_document(resolution)
        plan_fingerprint = _fingerprint(plan)
        rules = plan.get("rules")
        rule_count = len(rules) if isinstance(rules, list) else 0
        loadout = compact_source["loadout"]
        setup = compact_source["setup"]
        return {
            "valid": True,
            "profile": {
                "id": source["id"],
                "display_name": normalized_display_name,
                "family": "farm",
                "tier": source["tier"],
                "version": source["version"],
                "built_in": False,
                "editable": True,
                "published_at": None,
                "source_fingerprint": source_fingerprint,
                "base_fingerprint": base_fingerprint,
                "resolution_fingerprint": resolution_fingerprint,
                "plan_fingerprint": plan_fingerprint,
                "loadout": _copy_mapping(loadout),
                "setup": _copy_mapping(setup),
            },
            "source": source,
            "base_snapshot": base_snapshot,
            "base_resolution": base_resolution,
            "resolution": resolution,
            "plan": plan,
            "rule_count": rule_count,
            "summary": _profile_summary(compact_source, rule_count),
            "resolved_configuration": _copy_mapping(
                plan.get("run_configuration") or {}
            ),
        }

    def publish(
        self,
        raw_profile: object,
        *,
        expected_source_fingerprint: object = None,
        origin: str = "profile_facade_publication",
    ) -> dict[str, Any]:
        """Durably append a revision and advance the fixed latest facade."""

        if origin not in STRATEGY_PUBLICATION_ORIGINS:
            raise StrategyProfileError("Invalid server publication origin")
        with self._publish_lock:
            with self._catalog_write_lock():
                errors = self._prepare_history_under_file_lock()
                identifier = _draft_identifier(raw_profile)
                self._raise_lineage_history_error(identifier, errors)
                return self._publish_under_file_lock(
                    raw_profile,
                    expected_source_fingerprint=expected_source_fingerprint,
                    origin=origin,
                )

    def retire_strategy(
        self,
        strategy_id: object,
        *,
        expected_source_fingerprint: object,
    ) -> dict[str, Any]:
        """Remove one custom Strategy from active catalogs without erasing it."""

        identifier = _draft_identifier({"id": strategy_id})
        if identifier in _RESERVED_STRATEGY_IDS:
            raise StrategyProfileError(
                f"{identifier!r} is a bundled or reserved strategy name"
            )
        with self._publish_lock:
            with self._catalog_write_lock():
                errors = self._prepare_history_under_file_lock()
                self._raise_lineage_history_error(identifier, errors)
                return self._retire_under_file_lock(
                    identifier,
                    expected_source_fingerprint=expected_source_fingerprint,
                )

    @contextmanager
    def _catalog_write_lock(self) -> Iterator[None]:
        self.profile_directory.mkdir(parents=True, exist_ok=True)
        lock_path = self.profile_directory / ".strategy-profiles.write.lock"
        try:
            lock_handle = lock_path.open("a+", encoding="utf-8")
        except OSError as exc:
            raise StrategyProfileError(
                f"Unable to lock the strategy profile catalog: {exc}"
            ) from exc
        with lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                raise StrategyProfileError(
                    f"Unable to lock the strategy profile catalog: {exc}"
                ) from exc
            yield

    def _retire_under_file_lock(
        self,
        identifier: str,
        *,
        expected_source_fingerprint: object,
    ) -> dict[str, Any]:
        path = _profile_path(self.profile_directory, identifier)
        if not path.exists():
            raise StrategyProfileConflictError(
                f"Profile {identifier!r} no longer exists; reload the catalog"
            )
        if path.is_symlink() or not path.is_file():
            raise StrategyProfileConflictError(
                f"Existing profile path for {identifier!r} is not a regular file"
            )
        try:
            publication = _load_publication(path, expected_id=identifier)
        except StrategyProfileError as exc:
            raise StrategyProfileConflictError(
                f"Existing profile {identifier!r} is invalid and was preserved: {exc}"
            ) from exc

        expected = str(expected_source_fingerprint or "").strip()
        current = str(publication["source_fingerprint"])
        if not expected or expected != current:
            raise StrategyProfileConflictError(
                f"Profile {identifier!r} changed after it was opened; "
                "reload it before deleting"
            )

        retired_at = datetime.now().astimezone()
        retirement_directory = self.profile_directory / "retired"
        try:
            if retirement_directory.is_symlink() or (
                retirement_directory.exists()
                and not retirement_directory.is_dir()
            ):
                raise StrategyProfileConflictError(
                    "The retired Strategy archive is not a regular directory"
                )
            retirement_directory.mkdir(mode=0o700, exist_ok=True)
        except StrategyProfileConflictError:
            raise
        except OSError as exc:
            raise StrategyProfileError(
                f"Unable to create the retired Strategy archive: {exc}"
            ) from exc

        item = self._publication_item(publication)
        stamp = retired_at.strftime("%Y%m%dT%H%M%S%f%z")
        archive_name = (
            f"{identifier}.v{item['version']}.{stamp}."
            f"{current[:12]}.retired.yaml"
        )
        archive_path = retirement_directory / archive_name
        if archive_path.parent != retirement_directory:
            raise StrategyProfileError("Invalid retired Strategy archive path")
        if archive_path.exists() or archive_path.is_symlink():
            raise StrategyProfileConflictError(
                f"Retired archive target {archive_name!r} already exists"
            )

        moved = False
        try:
            os.replace(path, archive_path)
            moved = True
            self._fsync_directory(retirement_directory)
            self._fsync_directory(self.profile_directory)
        except OSError as exc:
            if moved:
                try:
                    os.replace(archive_path, path)
                    self._fsync_directory(retirement_directory)
                    self._fsync_directory(self.profile_directory)
                except OSError as rollback_exc:
                    raise StrategyProfileError(
                        "Unable to finish or roll back Strategy retirement; "
                        f"inspect retired/{archive_name}: {rollback_exc}"
                    ) from exc
            raise StrategyProfileError(
                f"Unable to retire Strategy {identifier!r}: {exc}"
            ) from exc

        with _PROFILE_ID_CACHE_LOCK:
            _PROFILE_ID_CACHE.pop(self.profile_directory, None)
        return {
            "id": identifier,
            "display_name": item["display_name"],
            "version": item["version"],
            "source_fingerprint": current,
            "retired_at": retired_at.isoformat(timespec="seconds"),
            "archive_name": archive_name,
            "recoverable": True,
        }

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _publish_under_file_lock(
        self,
        raw_profile: object,
        *,
        expected_source_fingerprint: object,
        origin: str,
    ) -> dict[str, Any]:
        identifier = _draft_identifier(raw_profile)
        if identifier in _RESERVED_STRATEGY_IDS:
            raise StrategyProfileError(
                f"{identifier!r} is a bundled or reserved strategy name"
            )
        validation = self.validate(raw_profile)
        return self._publish_validated_under_file_lock(
            validation,
            expected_source_fingerprint=expected_source_fingerprint,
            origin=origin,
            allow_retired_lineage=False,
        )

    def _publish_validated_under_file_lock(
        self,
        validation: Mapping[str, Any],
        *,
        expected_source_fingerprint: object,
        origin: str,
        allow_retired_lineage: bool,
    ) -> dict[str, Any]:
        if origin not in STRATEGY_PUBLICATION_ORIGINS:
            raise StrategyProfileError("Invalid server publication origin")
        raw_source = validation.get("source")
        if not isinstance(raw_source, Mapping):
            raise StrategyProfileError("Validated Strategy source is unavailable")
        source = normalize_strategy_source(raw_source)
        identifier = source["id"]
        existing = self._latest_publication(identifier)
        expected = str(expected_source_fingerprint or "").strip() or None
        if existing is not None:
            current = str(existing["source_fingerprint"])
            if expected != current:
                raise StrategyProfileConflictError(
                    f"Profile {identifier!r} changed after it was opened; "
                    "reload it before publishing"
                )
        elif expected is not None:
            raise StrategyProfileConflictError(
                f"Profile {identifier!r} no longer exists; reload the catalog"
            )

        versions = self._revision_versions(identifier)
        if existing is None and versions and not allow_retired_lineage:
            raise StrategyProfileConflictError(
                f"Strategy {identifier!r} has a retired immutable lineage; "
                "review and restore a retained revision instead of reusing its ID"
            )
        next_version = max(versions, default=0) + 1
        if source["version"] != next_version:
            source["version"] = next_version
        exact_base_snapshot = _copy_optional_mapping(
            validation.get("base_snapshot")
        )
        exact_base_resolution = _copy_optional_mapping(
            validation.get("base_resolution")
        )
        retained_resolution = _copy_optional_mapping(
            validation.get("resolution")
        )
        rebuilt = self._validate_normalized_source(
            source,
            base_snapshot=exact_base_snapshot,
            base_resolution=exact_base_resolution,
            retained_resolution=retained_resolution,
            display_name=source["display_name"],
        )
        audit_identity = _new_audit_identity()
        published_at = datetime.now().astimezone().isoformat(timespec="seconds")
        publication = {
            "schema_version": STRATEGY_PUBLICATION_SCHEMA_VERSION,
            "kind": "strategy_publication",
            "id": identifier,
            "display_name": rebuilt["profile"]["display_name"],
            "published_at": published_at,
            "publication_origin": origin,
            "audit_identity": audit_identity,
            "source_fingerprint": rebuilt["profile"]["source_fingerprint"],
            "base_fingerprint": rebuilt["profile"]["base_fingerprint"],
            "resolution_fingerprint": rebuilt["profile"][
                "resolution_fingerprint"
            ],
            "plan_fingerprint": rebuilt["profile"]["plan_fingerprint"],
            "source": rebuilt["source"],
            "base_snapshot": rebuilt["base_snapshot"],
            "base_resolution": rebuilt["base_resolution"],
            "resolution": rebuilt["resolution"],
            "plan": rebuilt["plan"],
        }
        revision = _revision_envelope_from_publication(
            publication,
            origin=origin,
            audit_identity=audit_identity,
        )
        self._commit_publication_transaction(
            identifier,
            publication=publication,
            revision=revision,
            previous=existing,
        )

        stored = self._latest_publication(identifier)
        if stored is None:
            raise StrategyProfileError(
                f"Published Strategy {identifier!r} has no latest facade"
            )
        retained = self._load_revision(identifier, next_version)
        if retained["publication_fingerprint"] != fingerprint_document(stored):
            raise StrategyProfileError(
                "Latest Strategy facade does not match its retained revision"
            )
        result = dict(rebuilt)
        result.pop("source", None)
        result.pop("base_snapshot", None)
        result.pop("base_resolution", None)
        result.pop("resolution", None)
        result.pop("plan", None)
        result["profile"] = self._publication_item(stored)
        result["published"] = True
        result["publication_origin"] = origin
        result["revision_fingerprint"] = fingerprint_document(retained)
        return result

    def _commit_publication_transaction(
        self,
        identifier: str,
        *,
        publication: Mapping[str, Any],
        revision: Mapping[str, Any],
        previous: Optional[Mapping[str, Any]],
    ) -> None:
        history_directory, transaction_directory = self._prepare_history_directories(
            create=True
        )
        version = _publication_version(publication)
        final_revision = self._revision_path(identifier, version)
        transaction_path = self._transaction_path(identifier)
        revision_stage = transaction_directory / f".{identifier}.revision.stage.yaml"
        latest_stage = transaction_directory / f".{identifier}.latest.stage.yaml"
        previous_stage = transaction_directory / f".{identifier}.previous.yaml"
        stage_paths = (revision_stage, latest_stage, previous_stage)
        if final_revision.exists() or final_revision.is_symlink():
            raise StrategyProfileConflictError(
                f"Immutable Strategy revision already exists: {final_revision.name}"
            )
        if transaction_path.exists() or transaction_path.is_symlink():
            raise StrategyProfileConflictError(
                f"Strategy publication transaction already exists for {identifier!r}"
            )
        if any(path.exists() or path.is_symlink() for path in stage_paths):
            raise StrategyProfileConflictError(
                f"Unreconciled Strategy publication staging exists for {identifier!r}"
            )

        previous_fingerprint = (
            fingerprint_document(previous) if previous is not None else None
        )
        transaction = {
            "schema_version": STRATEGY_TRANSACTION_SCHEMA_VERSION,
            "kind": "strategy_publication_transaction",
            "id": identifier,
            "logical_version": version,
            "created_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "publication_fingerprint": fingerprint_document(publication),
            "revision_fingerprint": fingerprint_document(revision),
            "had_previous_latest": previous is not None,
            "previous_publication_fingerprint": previous_fingerprint,
        }
        committed = False
        try:
            _atomic_create_immutable(
                transaction_directory,
                transaction_path,
                transaction,
                description="strategy publication transaction",
            )
            self._fault("transaction_record_durable")
            _atomic_create_immutable(
                transaction_directory,
                revision_stage,
                revision,
                description="strategy revision stage",
            )
            self._fault("revision_stage_durable")
            _atomic_create_immutable(
                transaction_directory,
                latest_stage,
                publication,
                description="strategy latest stage",
            )
            self._fault("latest_stage_durable")
            if previous is not None:
                _atomic_create_immutable(
                    transaction_directory,
                    previous_stage,
                    previous,
                    description="strategy previous-latest stage",
                )
            self._fault("previous_stage_durable")

            try:
                os.link(revision_stage, final_revision)
            except FileExistsError as exc:
                raise StrategyProfileConflictError(
                    f"Immutable Strategy revision already exists: {final_revision.name}"
                ) from exc
            revision_stage.unlink()
            self._fault("revision_linked")
            self._fsync_directory(history_directory)
            self._fault("history_fsynced")

            os.replace(latest_stage, _profile_path(self.profile_directory, identifier))
            self._fault("latest_replaced")
            self._fsync_directory(self.profile_directory)
            committed = True
            self._fault("latest_directory_fsynced")
        except Exception as exc:
            if committed:
                try:
                    self._cleanup_transaction(
                        transaction_path,
                        revision_stage,
                        latest_stage,
                        previous_stage,
                    )
                except Exception:
                    # History and latest are already durably consistent.  The
                    # retained journal is reconciled idempotently on reopen.
                    pass
                return
            try:
                self._rollback_publication_transaction(
                    identifier,
                    publication=publication,
                    revision=revision,
                    previous=previous,
                    transaction_path=transaction_path,
                    revision_stage=revision_stage,
                    latest_stage=latest_stage,
                    previous_stage=previous_stage,
                )
            except Exception as rollback_exc:
                raise StrategyProfileError(
                    "Strategy publication was interrupted and could not be "
                    f"rolled back deterministically: {rollback_exc}"
                ) from exc
            if isinstance(exc, StrategyProfileError):
                raise
            raise StrategyProfileError(
                f"Unable to publish Strategy {identifier!r}: {exc}"
            ) from exc

        try:
            self._cleanup_transaction(
                transaction_path,
                revision_stage,
                latest_stage,
                previous_stage,
            )
        except Exception:
            # The history/facade pair is already durably committed.  Keep any
            # surviving journal for deterministic reconciliation on reopen
            # instead of reporting a failure that invites a duplicate retry.
            pass
        try:
            self._fault("transaction_cleaned")
        except Exception:
            # Cleanup follows the durable commit point and cannot turn a
            # committed publication back into a failed one.
            pass
        with _PROFILE_ID_CACHE_LOCK:
            _PROFILE_ID_CACHE.pop(self.profile_directory, None)

    def _rollback_publication_transaction(
        self,
        identifier: str,
        *,
        publication: Mapping[str, Any],
        revision: Mapping[str, Any],
        previous: Optional[Mapping[str, Any]],
        transaction_path: Path,
        revision_stage: Path,
        latest_stage: Path,
        previous_stage: Path,
    ) -> None:
        latest_path = _profile_path(self.profile_directory, identifier)
        proposed_fingerprint = fingerprint_document(publication)
        previous_fingerprint = (
            fingerprint_document(previous) if previous is not None else None
        )
        if latest_path.exists() or latest_path.is_symlink():
            current = self._latest_publication(identifier)
            current_fingerprint = (
                fingerprint_document(current) if current is not None else None
            )
            if current_fingerprint == proposed_fingerprint:
                if previous is not None:
                    staged_previous = _load_publication(
                        previous_stage,
                        expected_id=identifier,
                    )
                    if fingerprint_document(staged_previous) != previous_fingerprint:
                        raise StrategyProfileConflictError(
                            "The previous latest backup changed during rollback"
                        )
                    os.replace(previous_stage, latest_path)
                else:
                    latest_path.unlink()
                self._fsync_directory(self.profile_directory)
            elif current_fingerprint != previous_fingerprint:
                raise StrategyProfileConflictError(
                    "Latest facade changed outside the failed publication"
                )
        elif previous is not None:
            staged_previous = _load_publication(
                previous_stage,
                expected_id=identifier,
            )
            if fingerprint_document(staged_previous) != previous_fingerprint:
                raise StrategyProfileConflictError(
                    "The previous latest backup changed during rollback"
                )
            os.replace(previous_stage, latest_path)
            self._fsync_directory(self.profile_directory)

        final_revision = self._revision_path(
            identifier,
            int(revision["logical_version"]),
        )
        if final_revision.exists() or final_revision.is_symlink():
            if final_revision.is_symlink() or not final_revision.is_file():
                raise StrategyProfileConflictError(
                    "The uncommitted revision target became unsafe during rollback"
                )
            loaded = self._load_revision(
                identifier,
                int(revision["logical_version"]),
            )
            if fingerprint_document(loaded) != fingerprint_document(revision):
                raise StrategyProfileConflictError(
                    "The uncommitted revision target changed during rollback"
                )
            final_revision.unlink()
            self._fsync_directory(final_revision.parent)
        self._cleanup_transaction(
            transaction_path,
            revision_stage,
            latest_stage,
            previous_stage,
        )

    def _cleanup_transaction(self, *paths: Path) -> None:
        changed = False
        ordered = sorted(
            paths,
            key=lambda path: path.name.endswith(".publication.yaml"),
        )
        for path in ordered:
            try:
                if path.is_symlink():
                    raise StrategyProfileConflictError(
                        f"Symbolic-link transaction artifact is unsupported: {path.name}"
                    )
                if path.exists():
                    path.unlink()
                    changed = True
            except FileNotFoundError:
                continue
        transaction_directory = self.profile_directory / (
            STRATEGY_TRANSACTION_DIRECTORY_NAME
        )
        if changed and transaction_directory.is_dir():
            self._fsync_directory(transaction_directory)

    def _fault(self, transition: str) -> None:
        if self._transaction_fault_hook is not None:
            self._transaction_fault_hook(transition)

    def _prepare_history(self) -> list[dict[str, str]]:
        if not self.profile_directory.exists():
            return []
        with self._publish_lock:
            try:
                with self._catalog_write_lock():
                    return self._prepare_history_under_file_lock()
            except StrategyProfileError as exc:
                self._audit_history_event(
                    "history-catalog",
                    f"Strategy history catalog error preserved: {exc}",
                )
                return [{"id": "history", "error": str(exc)}]

    def _prepare_history_under_file_lock(self) -> list[dict[str, str]]:
        errors: list[dict[str, str]] = []
        try:
            self._prepare_history_directories(create=False)
        except StrategyProfileError as exc:
            return [{"id": "history", "error": str(exc)}]
        errors.extend(self._recover_transactions_under_file_lock())
        errors.extend(self._adopt_existing_publications_under_file_lock())
        _, scan_errors = self._history_records_and_errors()
        errors.extend(scan_errors)
        return _deduplicate_catalog_errors(errors)

    def _prepare_history_directories(
        self,
        *,
        create: bool,
    ) -> tuple[Path, Path]:
        history_directory = self.profile_directory / STRATEGY_HISTORY_DIRECTORY_NAME
        transaction_directory = (
            self.profile_directory / STRATEGY_TRANSACTION_DIRECTORY_NAME
        )
        for directory, description in (
            (history_directory, "Strategy history"),
            (transaction_directory, "Strategy transaction"),
        ):
            if directory.is_symlink() or (
                directory.exists() and not directory.is_dir()
            ):
                raise StrategyProfileConflictError(
                    f"{description} storage is not a regular directory"
                )
            existed = directory.exists()
            if create:
                try:
                    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                except OSError as exc:
                    raise StrategyProfileError(
                        f"Unable to create {description.lower()} storage: {exc}"
                    ) from exc
                if not existed:
                    self._fsync_directory(self.profile_directory)
        return history_directory, transaction_directory

    def _recover_transactions_under_file_lock(self) -> list[dict[str, str]]:
        _, transaction_directory = self._prepare_history_directories(create=False)
        if not transaction_directory.exists():
            return []
        errors: list[dict[str, str]] = []
        try:
            paths = sorted(transaction_directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            return [{"id": "transactions", "error": str(exc)}]
        records: dict[str, Path] = {}
        for path in paths:
            match = _TRANSACTION_FILENAME_RE.fullmatch(path.name)
            if match is not None:
                records[match.group("id")] = path
                continue
            if _TRANSACTION_STAGE_FILENAME_RE.fullmatch(path.name) is not None:
                continue
            errors.append(
                {
                    "id": f"transactions/{path.name}",
                    "error": (
                        "symbolic-link Strategy transaction artifact is unsupported"
                        if path.is_symlink()
                        else "unrecognized Strategy transaction artifact"
                    ),
                }
            )

        for identifier, path in sorted(records.items()):
            try:
                if path.is_symlink() or not path.is_file():
                    raise StrategyProfileConflictError(
                        "symbolic-link or non-file transaction records are unsupported"
                    )
                raw = _load_yaml_mapping_limited_profile(
                    path,
                    "Strategy publication transaction",
                )
                transaction = _validate_transaction_record(
                    raw,
                    expected_id=identifier,
                )
                self._recover_transaction_under_file_lock(transaction)
                self._audit_history_event(
                    f"recovered-{identifier}-{transaction['logical_version']}",
                    "Recovered interrupted Strategy publication "
                    f"{identifier} version {transaction['logical_version']} "
                    "without changing selection or activation",
                )
            except StrategyProfileError as exc:
                error = {
                    "id": f"{identifier}@transaction",
                    "error": str(exc),
                }
                errors.append(error)
                self._audit_history_event(
                    f"transaction-conflict-{identifier}-{exc}",
                    "Strategy publication recovery conflict preserved for "
                    f"{identifier}: {exc}",
                )

        active_records = set(records)
        for path in paths:
            match = _TRANSACTION_STAGE_FILENAME_RE.fullmatch(path.name)
            if match is None or match.group("id") in active_records:
                continue
            try:
                if path.is_symlink() or not path.is_file():
                    raise StrategyProfileConflictError(
                        "unsafe orphan Strategy transaction artifact"
                    )
                path.unlink()
                self._fsync_directory(transaction_directory)
            except (OSError, StrategyProfileError) as exc:
                errors.append(
                    {
                        "id": f"transactions/{path.name}",
                        "error": str(exc),
                    }
                )
        return errors

    def _recover_transaction_under_file_lock(
        self,
        transaction: Mapping[str, Any],
    ) -> None:
        identifier = str(transaction["id"])
        version = int(transaction["logical_version"])
        history_directory, transaction_directory = self._prepare_history_directories(
            create=True
        )
        transaction_path = self._transaction_path(identifier)
        revision_stage = transaction_directory / f".{identifier}.revision.stage.yaml"
        latest_stage = transaction_directory / f".{identifier}.latest.stage.yaml"
        previous_stage = transaction_directory / f".{identifier}.previous.yaml"
        final_revision = self._revision_path(identifier, version)
        latest_path = _profile_path(self.profile_directory, identifier)
        expected_publication_fingerprint = str(
            transaction["publication_fingerprint"]
        )

        if final_revision.exists() or final_revision.is_symlink():
            revision = self._load_revision(identifier, version)
            if fingerprint_document(revision) != transaction["revision_fingerprint"]:
                raise StrategyProfileConflictError(
                    "Retained revision disagrees with its recovery transaction"
                )
            publication = revision["publication"]
            if fingerprint_document(publication) != expected_publication_fingerprint:
                raise StrategyProfileConflictError(
                    "Retained publication disagrees with its recovery transaction"
                )
            current = self._latest_publication(identifier)
            current_fingerprint = (
                fingerprint_document(current) if current is not None else None
            )
            previous_fingerprint = transaction.get(
                "previous_publication_fingerprint"
            )
            if current_fingerprint == expected_publication_fingerprint:
                pass
            elif current_fingerprint == previous_fingerprint or (
                current is None
                and not bool(transaction["had_previous_latest"])
            ):
                self._atomic_write(latest_path, publication)
            else:
                raise StrategyProfileConflictError(
                    "Latest facade changed outside the interrupted transaction"
                )
            self._fsync_directory(history_directory)
            self._fsync_directory(self.profile_directory)
            self._cleanup_transaction(
                transaction_path,
                revision_stage,
                latest_stage,
                previous_stage,
            )
            with _PROFILE_ID_CACHE_LOCK:
                _PROFILE_ID_CACHE.pop(self.profile_directory, None)
            return

        current = self._latest_publication(identifier)
        current_fingerprint = (
            fingerprint_document(current) if current is not None else None
        )
        previous_fingerprint = transaction.get("previous_publication_fingerprint")
        if current_fingerprint == expected_publication_fingerprint:
            if bool(transaction["had_previous_latest"]):
                if previous_stage.is_symlink() or not previous_stage.is_file():
                    raise StrategyProfileConflictError(
                        "Interrupted transaction lost its previous latest backup"
                    )
                staged_previous = _load_publication(
                    previous_stage,
                    expected_id=identifier,
                )
                if fingerprint_document(staged_previous) != previous_fingerprint:
                    raise StrategyProfileConflictError(
                        "Interrupted transaction previous backup disagrees with "
                        "its journal"
                    )
                os.replace(previous_stage, latest_path)
            else:
                latest_path.unlink()
            self._fsync_directory(self.profile_directory)
        elif current_fingerprint != previous_fingerprint or (
            current is None and bool(transaction["had_previous_latest"])
        ):
            raise StrategyProfileConflictError(
                "Latest facade changed outside the interrupted transaction"
            )
        self._cleanup_transaction(
            transaction_path,
            revision_stage,
            latest_stage,
            previous_stage,
        )

    def _adopt_existing_publications_under_file_lock(
        self,
    ) -> list[dict[str, str]]:
        candidates, errors = self._adoption_candidates()
        records, history_errors = self._history_records_and_errors()
        errors.extend(history_errors)
        if not candidates:
            return errors
        history_directory, _ = self._prepare_history_directories(create=True)
        for (identifier, version), publications in sorted(candidates.items()):
            fingerprints = {
                fingerprint_document(candidate["publication"])
                for candidate in publications
            }
            if len(fingerprints) != 1:
                message = (
                    f"conflicting retained evidence exists for {identifier} "
                    f"version {version}"
                )
                errors.append({"id": f"{identifier}@{version}", "error": message})
                self._audit_history_event(
                    f"adoption-conflict-{identifier}-{version}",
                    f"Strategy history adoption conflict preserved: {message}",
                )
                continue
            publication = publications[0]["publication"]
            existing = next(
                (
                    item
                    for item in records.get(identifier, [])
                    if int(item["logical_version"]) == version
                ),
                None,
            )
            if existing is not None:
                if existing["publication_fingerprint"] != fingerprint_document(
                    publication
                ):
                    message = (
                        "history fingerprint disagrees with existing latest or "
                        "retirement evidence"
                    )
                    errors.append(
                        {"id": f"{identifier}@{version}", "error": message}
                    )
                    self._audit_history_event(
                        f"adoption-disagreement-{identifier}-{version}",
                        "Strategy history adoption disagreement preserved for "
                        f"{identifier} version {version}",
                    )
                continue
            identity = _new_audit_identity()
            revision = _revision_envelope_from_publication(
                publication,
                origin="conservative_adoption",
                audit_identity=identity,
            )
            path = self._revision_path(identifier, version)
            if path.exists() or path.is_symlink():
                errors.append(
                    {
                        "id": f"{identifier}@{version}",
                        "error": (
                            "existing Strategy revision is invalid or ambiguous "
                            "and was preserved without adoption"
                        ),
                    }
                )
                continue
            try:
                _atomic_create_immutable(
                    history_directory,
                    path,
                    revision,
                    description="adopted Strategy revision",
                )
                records.setdefault(identifier, []).append(revision)
                records[identifier].sort(
                    key=lambda item: int(item["logical_version"])
                )
                self._audit_history_event(
                    f"adopted-{identifier}-{version}",
                    "Conservatively adopted existing Strategy publication "
                    f"{identifier} version {version} into immutable history; "
                    "the latest facade was not rewritten",
                )
            except (StrategyProfileError, StrategyAuthoringError) as exc:
                errors.append(
                    {"id": f"{identifier}@{version}", "error": str(exc)}
                )

        for identifier, lineage in records.items():
            latest = self._latest_publication(identifier)
            if latest is None or not lineage:
                continue
            latest_version = _publication_version(latest)
            max_version = max(int(item["logical_version"]) for item in lineage)
            matching = next(
                (
                    item
                    for item in lineage
                    if int(item["logical_version"]) == latest_version
                ),
                None,
            )
            if (
                latest_version != max_version
                or matching is None
                or matching["publication_fingerprint"]
                != fingerprint_document(latest)
            ):
                message = (
                    "active latest facade is ambiguous with immutable lineage "
                    f"history (facade version {latest_version}, history latest "
                    f"{max_version})"
                )
                errors.append({"id": identifier, "error": message})
                self._audit_history_event(
                    f"lineage-ambiguity-{identifier}-{latest_version}-{max_version}",
                    f"Strategy lineage ambiguity preserved for {identifier}: {message}",
                )
        return _deduplicate_catalog_errors(errors)

    def _adoption_candidates(
        self,
    ) -> tuple[
        dict[tuple[str, int], list[dict[str, Any]]],
        list[dict[str, str]],
    ]:
        candidates: dict[tuple[str, int], list[dict[str, Any]]] = {}
        errors: list[dict[str, str]] = []
        if self.profile_directory.is_dir() and not self.profile_directory.is_symlink():
            for path in sorted(self.profile_directory.glob("*.profile.yaml")):
                identifier = path.name.removesuffix(".profile.yaml")
                try:
                    if path.is_symlink() or not path.is_file():
                        raise StrategyProfileError(
                            "symbolic-link publications are not supported"
                        )
                    publication = _load_publication(path, expected_id=identifier)
                    version = _publication_version(publication)
                    if version < 1:
                        raise StrategyProfileError(
                            "Profile publication has no positive logical version"
                        )
                    candidates.setdefault((identifier, version), []).append(
                        {"publication": publication, "evidence": "latest"}
                    )
                except StrategyProfileError as exc:
                    errors.append({"id": identifier, "error": str(exc)})

        retired_directory = self.profile_directory / "retired"
        if retired_directory.is_symlink() or (
            retired_directory.exists() and not retired_directory.is_dir()
        ):
            errors.append(
                {
                    "id": "retired",
                    "error": "The retired Strategy archive is not a regular directory",
                }
            )
            return candidates, errors
        if not retired_directory.exists():
            return candidates, errors
        try:
            retired_paths = sorted(retired_directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            errors.append({"id": "retired", "error": str(exc)})
            return candidates, errors
        for path in retired_paths:
            if not path.name.endswith(".retired.yaml"):
                continue
            try:
                if path.is_symlink() or not path.is_file():
                    raise StrategyProfileError(
                        "symbolic-link retirement evidence is not supported"
                    )
                publication = _load_publication_unknown_id(
                    path,
                    verify_builder=False,
                )
                identifier = str(publication["id"])
                version = _publication_version(publication)
                if version < 1:
                    raise StrategyProfileError(
                        "Retired publication has no positive logical version"
                    )
                candidates.setdefault((identifier, version), []).append(
                    {"publication": publication, "evidence": "retired"}
                )
            except StrategyProfileError as exc:
                errors.append(
                    {"id": f"retired/{path.name}", "error": str(exc)}
                )
        return candidates, errors

    def _history_records_and_errors(
        self,
    ) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
        history_directory, _ = self._prepare_history_directories(create=False)
        records: dict[str, list[dict[str, Any]]] = {}
        errors: list[dict[str, str]] = []
        if not history_directory.exists():
            return records, errors
        try:
            paths = sorted(history_directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            return records, [{"id": "history", "error": str(exc)}]
        seen: set[tuple[str, int]] = set()
        for path in paths:
            if path.is_symlink():
                errors.append(
                    {
                        "id": f"history/{path.name}",
                        "error": "symbolic-link Strategy revision is unsupported",
                    }
                )
                continue
            if not path.is_file():
                errors.append(
                    {
                        "id": f"history/{path.name}",
                        "error": "non-file Strategy revision is unsupported",
                    }
                )
                continue
            match = _REVISION_FILENAME_RE.fullmatch(path.name)
            if match is None:
                errors.append(
                    {
                        "id": f"history/{path.name}",
                        "error": "invalid Strategy revision filename",
                    }
                )
                continue
            identifier = match.group("id")
            version = int(match.group("version"))
            key = (identifier, version)
            if key in seen:
                errors.append(
                    {
                        "id": f"{identifier}@{version}",
                        "error": "duplicate Strategy logical version",
                    }
                )
                continue
            seen.add(key)
            try:
                revision = self._load_revision(identifier, version)
                records.setdefault(identifier, []).append(revision)
            except StrategyProfileError as exc:
                errors.append(
                    {"id": f"{identifier}@{version}", "error": str(exc)}
                )
        for lineage in records.values():
            lineage.sort(key=lambda item: int(item["logical_version"]))
        return records, errors

    def _history_catalog_under_file_lock(
        self,
        *,
        identifier: Optional[str],
        preparation_errors: list[dict[str, str]],
    ) -> dict[str, Any]:
        try:
            records, scan_errors = self._history_records_and_errors()
        except StrategyProfileError as exc:
            records = {}
            scan_errors = [{"id": "history", "error": str(exc)}]
        errors = _deduplicate_catalog_errors(preparation_errors + scan_errors)
        if identifier is not None:
            records = (
                {identifier: records[identifier]}
                if identifier in records
                else {}
            )
            errors = [
                item
                for item in errors
                if _history_error_applies(identifier, item["id"])
            ]
        lineages: list[dict[str, Any]] = []
        for lineage_id, revisions in sorted(records.items()):
            latest: Optional[dict[str, Any]]
            latest_error: Optional[str] = None
            try:
                latest = self._latest_publication(lineage_id)
            except StrategyProfileError as exc:
                latest = None
                latest_error = str(exc)
                errors.append({"id": lineage_id, "error": latest_error})
            latest_fingerprint = (
                fingerprint_document(latest) if latest is not None else None
            )
            retired = latest is None and latest_error is None
            summaries = [
                self._revision_summary(
                    revision,
                    latest=latest,
                    lineage_retired=retired,
                    lineage_latest_version=int(revisions[-1]["logical_version"]),
                )
                for revision in reversed(revisions)
            ]
            newest = revisions[-1]
            source = newest["source"]
            lineage_errors = [
                item["error"]
                for item in errors
                if _history_error_applies(lineage_id, item["id"])
            ]
            lineages.append(
                {
                    "id": lineage_id,
                    "display_name": (
                        latest["display_name"]
                        if latest is not None
                        else newest["display_name"]
                    ),
                    "family": source["family"],
                    "tier": source["tier"],
                    "active_latest": latest is not None,
                    "retired": retired,
                    "latest_version": int(newest["logical_version"]),
                    "current_latest_version": (
                        _publication_version(latest)
                        if latest is not None
                        else None
                    ),
                    "latest_source_fingerprint": (
                        latest["source_fingerprint"]
                        if latest is not None
                        else None
                    ),
                    "latest_publication_fingerprint": latest_fingerprint,
                    "lineage_fingerprint": fingerprint_document(
                        {
                            "id": lineage_id,
                            "revisions": [
                                fingerprint_document(item) for item in revisions
                            ],
                            "latest": latest_fingerprint,
                        }
                    ),
                    "revisions": summaries,
                    "warnings": lineage_errors,
                }
            )
        return {
            "schema_version": STRATEGY_HISTORY_API_SCHEMA_VERSION,
            "lineages": lineages,
            "errors": errors,
            "newest_first": True,
            "expanded_plan_exposed": False,
        }

    def _revision_summary(
        self,
        revision: Mapping[str, Any],
        *,
        latest: Optional[Mapping[str, Any]],
        lineage_retired: bool,
        lineage_latest_version: int,
    ) -> dict[str, Any]:
        source = revision["source"]
        base = source.get("base")
        publication_fingerprint = str(revision["publication_fingerprint"])
        latest_matches = (
            latest is not None
            and fingerprint_document(latest) == publication_fingerprint
        )
        current_validation = _current_revision_validation(revision)
        warnings = list(current_validation["warnings"])
        if revision["publication_schema_version"] == STRATEGY_PROFILE_SCHEMA_VERSION:
            warnings.append(
                "Schema-1 source was conservatively projected as explicit local intent; the exact publication and plan remain retained."
            )
        if revision["publication_origin"] == "conservative_adoption":
            warnings.append(
                "Conservatively adopted from an existing latest or retirement publication."
            )
        return {
            "strategy_id": revision["id"],
            "display_name": revision["display_name"],
            "logical_version": revision["logical_version"],
            "published_at": revision["published_at"],
            "status": (
                "active_latest"
                if latest_matches
                else "retired_latest"
                if lineage_retired
                and int(revision["logical_version"])
                == lineage_latest_version
                else "historical"
            ),
            "active_latest": latest_matches,
            "retired_lineage": lineage_retired,
            "source_fingerprint": revision["source_fingerprint"],
            "normalized_source_fingerprint": revision[
                "normalized_source_fingerprint"
            ],
            "base_fingerprint": revision["base_fingerprint"],
            "resolution_fingerprint": revision["resolution_fingerprint"],
            "plan_fingerprint": revision["plan_fingerprint"],
            "publication_fingerprint": publication_fingerprint,
            "revision_fingerprint": fingerprint_document(revision),
            "pinned_base_id": base.get("id") if isinstance(base, Mapping) else None,
            "pinned_base_revision": (
                base.get("revision") if isinstance(base, Mapping) else None
            ),
            "tier": source["tier"],
            "family": source["family"],
            "publication_origin": revision["publication_origin"],
            "audit_identity": _copy_mapping(revision["audit_identity"]),
            "publication_schema_version": revision[
                "publication_schema_version"
            ],
            "rule_count": revision["rule_count"],
            "current_validation_valid": current_validation["valid"],
            "validation_errors": current_validation["errors"],
            "warnings": warnings,
        }

    def _revision_versions(self, identifier: str) -> list[int]:
        records, errors = self._history_records_and_errors()
        self._raise_lineage_history_error(identifier, errors)
        return [
            int(item["logical_version"])
            for item in records.get(identifier, [])
        ]

    def _load_revision(
        self,
        identifier: str,
        logical_version: int,
    ) -> dict[str, Any]:
        path = self._revision_path(identifier, logical_version)
        if path.is_symlink():
            raise StrategyProfileError(
                "symbolic-link Strategy revisions are unsupported"
            )
        if not path.is_file():
            raise StrategyProfileError(
                f"Strategy revision {identifier}@{logical_version} is unavailable"
            )
        raw = _load_yaml_mapping_limited_profile(path, "Strategy revision")
        return _validate_revision_envelope(
            raw,
            expected_id=identifier,
            expected_version=logical_version,
        )

    def _latest_publication(
        self,
        identifier: str,
    ) -> Optional[dict[str, Any]]:
        path = _profile_path(self.profile_directory, identifier)
        if not path.exists() and not path.is_symlink():
            return None
        if path.is_symlink() or not path.is_file():
            raise StrategyProfileConflictError(
                f"Existing profile path for {identifier!r} is not a regular file"
            )
        try:
            return _load_publication(path, expected_id=identifier)
        except StrategyProfileError as exc:
            raise StrategyProfileConflictError(
                f"Existing profile {identifier!r} is invalid and was preserved: {exc}"
            ) from exc

    def _revision_path(self, identifier: str, logical_version: int) -> Path:
        history_directory = self.profile_directory / STRATEGY_HISTORY_DIRECTORY_NAME
        path = history_directory / f"{identifier}.strategy.{logical_version}.yaml"
        if path.parent != history_directory:
            raise StrategyProfileError("Invalid Strategy revision path")
        return path

    def _transaction_path(self, identifier: str) -> Path:
        transaction_directory = (
            self.profile_directory / STRATEGY_TRANSACTION_DIRECTORY_NAME
        )
        path = transaction_directory / f"{identifier}.publication.yaml"
        if path.parent != transaction_directory:
            raise StrategyProfileError("Invalid Strategy transaction path")
        return path

    def _raise_lineage_history_error(
        self,
        identifier: str,
        errors: list[dict[str, str]],
    ) -> None:
        relevant = [
            item["error"]
            for item in errors
            if _history_error_applies(identifier, item["id"])
        ]
        if relevant:
            raise StrategyProfileConflictError(
                f"Strategy {identifier!r} history is ambiguous or corrupt: "
                + "; ".join(relevant)
            )

    def _audit_history_event(self, key: str, message: str) -> None:
        if key in self._reported_history_events:
            return
        self._reported_history_events.add(key)
        if self._audit_callback is None:
            return
        try:
            self._audit_callback(message)
        except Exception:
            # Audit failure cannot justify rewriting or discarding evidence.
            return

    def _restore_preview_under_file_lock(
        self,
        identifier: str,
        logical_version: int,
        *,
        expected_revision_fingerprint: object,
        expected_latest_source_fingerprint: object,
        require_optimistic_state: bool,
    ) -> dict[str, Any]:
        revision = self._load_revision(identifier, logical_version)
        revision_fingerprint = fingerprint_document(revision)
        supplied_revision_fingerprint = str(
            expected_revision_fingerprint or ""
        ).strip()
        if require_optimistic_state and (
            not supplied_revision_fingerprint
            or supplied_revision_fingerprint != revision_fingerprint
        ):
            raise StrategyProfileConflictError(
                "The selected historical revision changed or no longer matches "
                "the reviewed fingerprint"
            )

        latest = self._latest_publication(identifier)
        latest_source_fingerprint = (
            str(latest["source_fingerprint"]) if latest is not None else None
        )
        supplied_latest = (
            str(expected_latest_source_fingerprint or "").strip() or None
        )
        if require_optimistic_state and supplied_latest != latest_source_fingerprint:
            raise StrategyProfileConflictError(
                f"Profile {identifier!r} latest state changed after history was "
                "opened; refresh history before restoring"
            )
        if (
            require_optimistic_state
            and latest is not None
            and fingerprint_document(latest)
            == revision["publication_fingerprint"]
        ):
            raise StrategyProfileError(
                "The selected revision is already the current latest publication"
            )

        validation_errors: list[dict[str, str]] = []
        current_projection = None
        if latest is not None:
            current_projection = _publication_projection(latest)
            try:
                current_validation = self._validate_normalized_source(
                    current_projection["source"],
                    base_snapshot=current_projection["base_snapshot"],
                    base_resolution=current_projection.get("base_resolution"),
                    retained_resolution=current_projection["resolution"],
                )
                if current_validation["plan"] != current_projection["plan"]:
                    raise StrategyProfileError(
                        "The current latest publication no longer rebuilds exactly "
                        "under trusted builder code"
                    )
            except StrategyProfileError as exc:
                if require_optimistic_state:
                    raise
                validation_errors.append(
                    {
                        "code": "current_latest_validation",
                        "message": str(exc),
                    }
                )

        historical_consistent = True
        try:
            historical_validation = self._validate_normalized_source(
                revision["source"],
                base_snapshot=revision.get("base_snapshot"),
                base_resolution=revision.get("base_resolution"),
                retained_resolution=revision.get("resolution"),
            )
            if historical_validation["resolution"] != revision["resolution"]:
                raise StrategyProfileError(
                    "The selected revision resolution is inconsistent"
                )
            if historical_validation["plan"] != revision["plan"]:
                raise StrategyProfileError(
                    "The selected revision no longer rebuilds exactly under "
                    "trusted builder code"
                )
        except StrategyProfileError as exc:
            if require_optimistic_state:
                raise
            historical_consistent = False
            validation_errors.append(
                {
                    "code": "historical_revision_validation",
                    "message": str(exc),
                }
            )

        versions = self._revision_versions(identifier)
        next_version = max(versions, default=0) + 1
        candidate_source = _copy_mapping(revision["source"])
        candidate_source["version"] = next_version
        candidate_validation: dict[str, Any]
        if historical_consistent:
            try:
                candidate_validation = self._validate_normalized_source(
                    candidate_source,
                    base_snapshot=revision.get("base_snapshot"),
                    base_resolution=revision.get("base_resolution"),
                    retained_resolution=revision.get("resolution"),
                )
            except StrategyProfileError as exc:
                if require_optimistic_state:
                    raise
                validation_errors.append(
                    {
                        "code": "restore_candidate_validation",
                        "message": str(exc),
                    }
                )
                candidate_validation = _unvalidated_restore_candidate(
                    revision,
                    candidate_source,
                )
        else:
            candidate_validation = _unvalidated_restore_candidate(
                revision,
                candidate_source,
            )
        comparison = self._semantic_restore_comparison(
            identifier,
            current_projection=current_projection,
            historical_revision=revision,
            candidate_validation=candidate_validation,
            validation_errors=validation_errors,
        )
        review_fingerprint = fingerprint_document(
            {
                "kind": "strategy_restore_review",
                "strategy_id": identifier,
                "historical_logical_version": logical_version,
                "historical_revision_fingerprint": revision_fingerprint,
                "current_latest_source_fingerprint": latest_source_fingerprint,
                "next_logical_version": next_version,
                "candidate_fingerprints": candidate_validation["profile"],
                "comparison": comparison,
            }
        )
        return {
            "valid": not validation_errors,
            "published": False,
            "strategy_id": identifier,
            "historical_logical_version": logical_version,
            "historical_revision_fingerprint": revision_fingerprint,
            "current_latest_source_fingerprint": latest_source_fingerprint,
            "next_logical_version": next_version,
            "candidate": {
                "source_fingerprint": candidate_validation["profile"][
                    "source_fingerprint"
                ],
                "base_fingerprint": candidate_validation["profile"][
                    "base_fingerprint"
                ],
                "resolution_fingerprint": candidate_validation["profile"][
                    "resolution_fingerprint"
                ],
                "plan_fingerprint": candidate_validation["profile"][
                    "plan_fingerprint"
                ],
                "rule_count": candidate_validation["rule_count"],
            },
            "comparison": comparison,
            "reviewed_restore_fingerprint": review_fingerprint,
            "restore_publishes_new_revision": True,
            "publication_activates_strategy": False,
            "expanded_plan_exposed": False,
            "_candidate": {"validation": candidate_validation},
        }

    def _semantic_restore_comparison(
        self,
        identifier: str,
        *,
        current_projection: Optional[Mapping[str, Any]],
        historical_revision: Mapping[str, Any],
        candidate_validation: Mapping[str, Any],
        validation_errors: list[dict[str, str]],
    ) -> dict[str, Any]:
        candidate_source = candidate_validation["source"]
        candidate_resolution = candidate_validation["resolution"]
        if current_projection is None:
            before_source = {
                "schema_version": AUTHORING_SCHEMA_VERSION,
                "kind": "strategy",
                "id": identifier,
                "display_name": candidate_source["display_name"],
                "family": candidate_source["family"],
                "tier": candidate_source["tier"],
                "version": 1,
                "settings": {},
            }
            before_resolution = analyze_strategy_source(before_source)[
                "resolution"
            ]
            before_base = None
            before_base_resolution = None
            before_base_fingerprint = fingerprint_document({})
            before_plan = None
            before_plan_fingerprint = None
            before_rule_count = 0
        else:
            before_source = current_projection["source"]
            before_resolution = current_projection["resolution"]
            before_base = current_projection.get("base_snapshot")
            before_base_resolution = current_projection.get("base_resolution")
            before_base_fingerprint = current_projection["base_fingerprint"]
            before_plan = current_projection["plan"]
            before_plan_fingerprint = current_projection["plan_fingerprint"]
            before_rule_count = _plan_rule_count(before_plan)

        source_changes = diff_source_documents(before_source, candidate_source)
        effective_changes = diff_strategy_resolutions(
            before_resolution,
            candidate_resolution,
        )
        after_base = historical_revision.get("base_snapshot")
        after_base_resolution = historical_revision.get("base_resolution")
        after_base_fingerprint = historical_revision["base_fingerprint"]
        before_reference = before_source.get("base")
        after_reference = candidate_source.get("base")
        base_changes = {
            "changed": (
                before_reference != after_reference
                or before_base_fingerprint != after_base_fingerprint
            ),
            "before_reference": _copy_optional_mapping(before_reference),
            "after_reference": _copy_optional_mapping(after_reference),
            "before_fingerprint": before_base_fingerprint,
            "after_fingerprint": after_base_fingerprint,
            "embedded_snapshot_changed": fingerprint_document(before_base or {})
            != fingerprint_document(after_base or {}),
            "embedded_definition_resolution_changed": fingerprint_document(
                before_base_resolution or {}
            )
            != fingerprint_document(after_base_resolution or {}),
        }
        local_override_changes = _filter_directive_changes(
            source_changes,
            policy="override",
        )
        explicit_ignore_changes = _filter_directive_changes(
            source_changes,
            policy="ignore",
        )
        candidate_plan = candidate_validation["plan"]
        candidate_plan_fingerprint = candidate_validation["profile"][
            "plan_fingerprint"
        ]
        plan_changes = {
            "changed": before_plan_fingerprint != candidate_plan_fingerprint,
            "before_fingerprint": before_plan_fingerprint,
            "after_fingerprint": candidate_plan_fingerprint,
            "before_rule_count": before_rule_count,
            "after_rule_count": candidate_validation["rule_count"],
            "rule_count_change": candidate_validation["rule_count"]
            - before_rule_count,
        }
        directive_change_count = sum(
            len(source_changes[name]) for name in ("added", "removed", "changed")
        )
        plans_metadata_equal = (
            before_plan is not None
            and _plans_equal_except_publication_metadata(
                before_plan,
                candidate_plan,
            )
        )
        metadata_only = bool(
            source_changes["metadata_changes"]
            and directive_change_count == 0
            and effective_changes["change_count"] == 0
            and not base_changes["changed"]
            and plans_metadata_equal
        )
        return {
            "source_changes": source_changes,
            "effective_changes": effective_changes,
            "base_snapshot_changes": base_changes,
            "local_override_changes": local_override_changes,
            "explicit_ignore_changes": explicit_ignore_changes,
            "generated_plan_changes": plan_changes,
            "metadata_only": metadata_only,
            "validation": {
                "valid": not validation_errors,
                "errors": validation_errors,
            },
            "historical_intent_preserved": not validation_errors,
            "restore_publishes_new_revision": True,
            "publication_activates_strategy": False,
        }

    def _authoring_source_from_draft(
        self,
        raw_profile: object,
    ) -> tuple[dict[str, Any], str]:
        if not isinstance(raw_profile, Mapping):
            raise StrategyProfileError("profile must be an object")
        is_authoring = (
            raw_profile.get("kind") == "strategy"
            or (
                raw_profile.get("schema_version")
                in {LEGACY_AUTHORING_SCHEMA_VERSION, AUTHORING_SCHEMA_VERSION}
                and "settings" in raw_profile
            )
        )
        if is_authoring:
            identifier = _draft_identifier(raw_profile)
            if identifier in _RESERVED_STRATEGY_IDS:
                raise StrategyProfileError(
                    f"{identifier!r} is a bundled or reserved strategy name; "
                    "clone it under a new id"
                )
            try:
                source = normalize_strategy_source(
                    upgrade_authoring_source_schema(raw_profile),
                    version=self._next_profile_version(identifier),
                )
            except StrategyAuthoringError as exc:
                raise StrategyProfileError(str(exc)) from exc
            return source, source["display_name"]

        compact_source, display_name = self._normalize_draft(raw_profile)
        try:
            source = legacy_farm_source_to_strategy_source(
                compact_source,
                display_name=display_name,
            )
        except StrategyAuthoringError as exc:
            raise StrategyProfileError(str(exc)) from exc
        return source, display_name

    def _load_base_evidence(
        self,
        source: Mapping[str, Any],
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        base = source.get("base")
        if not isinstance(base, Mapping):
            return None, None
        try:
            publication = self.base_store.load(base["id"], base["revision"])
        except StrategyAuthoringError as exc:
            raise StrategyProfileError(str(exc)) from exc
        return (
            _copy_mapping(publication["snapshot"]),
            base_publication_resolution(publication),
        )

    def _base_evidence_for_source(
        self,
        source: Mapping[str, Any],
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        base = source.get("base")
        if not isinstance(base, Mapping):
            return None, None
        current = self._existing_authoring_state(str(source.get("id") or ""))
        if (
            current is not None
            and current["source"].get("base") == base
            and current["base_snapshot"] is not None
        ):
            return (
                _copy_mapping(current["base_snapshot"]),
                _copy_optional_mapping(current.get("base_resolution")),
            )
        return self._load_base_evidence(source)

    def _existing_authoring_state(
        self,
        strategy_id: object,
    ) -> Optional[dict[str, Any]]:
        identifier = normalize_strategy_id(strategy_id)
        if identifier is None:
            return None
        if identifier in {"farm_t18", "farm_t19"}:
            source = self.authoring_source(identifier)
            if source is None:
                return None
            resolution = resolve_strategy_source(source)
            return {
                "source": source,
                "base_snapshot": None,
                "base_resolution": None,
                "resolution": resolution,
                "legacy_converted": True,
                "source_fingerprint": None,
            }
        if identifier in _RESERVED_STRATEGY_IDS:
            return None
        path = _profile_path(self.profile_directory, identifier)
        if not path.is_file() or path.is_symlink():
            return None
        publication = _load_publication(path, expected_id=identifier)
        source = self.authoring_source(identifier)
        if source is None:
            return None
        raw_base_snapshot = publication.get("base_snapshot")
        base_snapshot = (
            _copy_mapping(raw_base_snapshot)
            if publication["schema_version"] == STRATEGY_PUBLICATION_SCHEMA_VERSION
            and isinstance(raw_base_snapshot, Mapping)
            else None
        )
        raw_base_resolution = publication.get("base_resolution")
        base_resolution = (
            _copy_mapping(raw_base_resolution)
            if isinstance(raw_base_resolution, Mapping)
            else None
        )
        retained_resolution = (
            publication.get("resolution")
            if publication["schema_version"]
            == STRATEGY_PUBLICATION_SCHEMA_VERSION
            else None
        )
        resolution = resolve_strategy_source(
            source,
            base_snapshot,
            base_resolution_snapshot=base_resolution,
            retained_resolution=retained_resolution,
            require_base_definition_snapshots=(
                source["schema_version"] == AUTHORING_SCHEMA_VERSION
                and base_snapshot is not None
            ),
        )
        return {
            "source": source,
            "base_snapshot": base_snapshot,
            "base_resolution": base_resolution,
            "resolution": resolution,
            "legacy_converted": (
                publication["schema_version"] == STRATEGY_PROFILE_SCHEMA_VERSION
            ),
            "source_fingerprint": publication["source_fingerprint"],
        }

    def _authoring_catalog_item(
        self,
        summary: Mapping[str, Any],
        base_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        item = _copy_mapping(summary)
        identifier = str(summary.get("id") or "")
        family = str(summary.get("family") or "").strip().lower()
        if family != "farm" or identifier in {"tournament", "none"}:
            item.update(
                {
                    "authoring_supported": False,
                    "editable": False,
                    "source": None,
                    "resolution": None,
                    "compatible_base_revisions": [],
                    "base_update": None,
                    "read_only_reason": (
                        "Tournament uses a dedicated protected strategy family."
                        if identifier == "tournament"
                        else "No Strategy contains no authorable runtime plan."
                    ),
                }
            )
            return item

        try:
            state = self._existing_authoring_state(identifier)
        except (StrategyAuthoringError, StrategyProfileError) as exc:
            raise StrategyProfileError(str(exc)) from exc
        if state is None:
            raise StrategyProfileError("authoring source is unavailable")
        source = state["source"]
        compatible = [
            {
                "id": base["id"],
                "display_name": base["display_name"],
                "revision": base["latest_revision"],
                "source_fingerprint": base["source_fingerprint"],
            }
            for base in base_items
            if base["family"] == source["family"]
        ]
        base_update = None
        pinned = source.get("base")
        if isinstance(pinned, Mapping):
            latest = next(
                (base for base in compatible if base["id"] == pinned["id"]),
                None,
            )
            if latest is not None and latest["revision"] > pinned["revision"]:
                base_update = {
                    "id": latest["id"],
                    "display_name": latest["display_name"],
                    "pinned_revision": pinned["revision"],
                    "latest_revision": latest["revision"],
                    "source_fingerprint": latest["source_fingerprint"],
                }
        item.update(
            {
                "authoring_supported": True,
                "source": _copy_mapping(source),
                "resolution": _copy_mapping(state["resolution"]),
                "normalized_source_fingerprint": fingerprint_document(source),
                "legacy_converted": state["legacy_converted"],
                "compatible_base_revisions": compatible,
                "base_update": base_update,
                "read_only_reason": (
                    "Bundled Strategies are immutable; clone this source to edit it."
                    if summary.get("built_in")
                    else None
                ),
            }
        )
        return item

    def _next_profile_version(self, identifier: str) -> int:
        versions: list[int] = []
        if self.profile_directory.exists():
            records, errors = self._history_records_and_errors()
            self._raise_lineage_history_error(identifier, errors)
            versions.extend(
                int(item["logical_version"])
                for item in records.get(identifier, [])
            )
            candidates, candidate_errors = self._adoption_candidates()
            self._raise_lineage_history_error(identifier, candidate_errors)
            for (candidate_id, version), publications in candidates.items():
                if candidate_id != identifier:
                    continue
                fingerprints = {
                    fingerprint_document(item["publication"])
                    for item in publications
                }
                if len(fingerprints) != 1:
                    raise StrategyProfileConflictError(
                        f"Strategy {identifier!r} has conflicting version "
                        f"{version} lineage evidence"
                    )
                versions.append(version)
        return max(versions, default=0) + 1

    def _normalize_draft(
        self,
        raw_profile: object,
    ) -> tuple[dict[str, Any], str]:
        if not isinstance(raw_profile, Mapping):
            raise StrategyProfileError("profile must be an object")
        identifier = _draft_identifier(raw_profile)
        if identifier in _RESERVED_STRATEGY_IDS:
            raise StrategyProfileError(
                f"{identifier!r} is a bundled or reserved strategy name; clone it under a new id"
            )

        display_name = str(raw_profile.get("display_name") or "").strip()
        if not display_name:
            display_name = _default_display_name(identifier)
        if len(display_name) > 80:
            raise StrategyProfileError("display_name must be at most 80 characters")

        tier = raw_profile.get("tier")
        if isinstance(tier, bool):
            raise StrategyProfileError("tier must be an integer")
        try:
            tier = int(tier)
        except (TypeError, ValueError) as exc:
            raise StrategyProfileError("tier must be an integer") from exc
        if not 1 <= tier <= 100:
            raise StrategyProfileError("tier must be between 1 and 100")

        version = self._next_profile_version(identifier)

        raw_loadout = raw_profile.get("loadout")
        if not isinstance(raw_loadout, Mapping):
            raise StrategyProfileError("loadout must be an object")
        missing = [key for key in FARM_LOADOUT_KEYS if key not in raw_loadout]
        extra = sorted(set(raw_loadout) - set(FARM_LOADOUT_KEYS))
        if missing or extra:
            raise StrategyProfileError(
                "loadout must define exactly modules, damage_slider, "
                f"orb_distance, and target_priority (missing={missing}, extra={extra})"
            )

        loadout = {
            "modules": _normalize_preset_policy(
                "modules", raw_loadout["modules"]
            ),
            "damage_slider": _normalize_damage_policy(
                raw_loadout["damage_slider"]
            ),
            "orb_distance": _normalize_preset_policy(
                "orb_distance", raw_loadout["orb_distance"]
            ),
            "target_priority": _normalize_preset_policy(
                "target_priority", raw_loadout["target_priority"]
            ),
        }
        setup = _normalize_farm_setup(raw_profile.get("setup"))
        source = {
            "meta": {
                "name": identifier,
                "family": "farm",
                "tier": tier,
                "version": version,
            },
            "builder": "farm",
            "run_profile": "farm",
            "loadout": loadout,
            "setup": setup,
        }
        return source, display_name

    def _builtin_item(self, identifier: str) -> dict[str, Any]:
        if identifier == "none":
            return {
                "id": "none",
                "display_name": "No strategy",
                "family": "none",
                "tier": None,
                "version": 1,
                "built_in": True,
                "editable": False,
                "published_at": None,
                "source_fingerprint": None,
                "plan_fingerprint": None,
                "loadout": None,
                "setup": None,
            }
        source_path = self.builtin_strategies_directory / f"{identifier}.source.yaml"
        plan_path = self.builtin_strategies_directory / f"{identifier}.strategy.yaml"
        source = _load_yaml_mapping(source_path, "bundled strategy source")
        plan = _load_yaml_mapping(plan_path, "bundled generated strategy")
        meta = source.get("meta")
        if not isinstance(meta, Mapping) or meta.get("name") != identifier:
            raise StrategyProfileError(
                f"Bundled source {source_path} has the wrong meta.name"
            )
        family = str(meta.get("family") or identifier).strip().lower()
        return {
            "id": identifier,
            "display_name": _default_display_name(identifier),
            "family": family,
            "tier": meta.get("tier"),
            "version": int(meta.get("version") or 1),
            "built_in": True,
            "editable": False,
            "published_at": None,
            "source_fingerprint": _fingerprint(source),
            "plan_fingerprint": _fingerprint(plan),
            "loadout": _copy_mapping(source.get("loadout") or {}) or None,
            "setup": (
                _normalize_farm_setup(source.get("setup"))
                if family == "farm"
                else None
            ),
        }

    @staticmethod
    def _publication_item(publication: Mapping[str, Any]) -> dict[str, Any]:
        source = publication["source"]
        if publication["schema_version"] == STRATEGY_PUBLICATION_SCHEMA_VERSION:
            compact_source = farm_source_from_resolution(
                source,
                publication["resolution"],
            )
            meta = compact_source["meta"]
            loadout = compact_source["loadout"]
            setup = compact_source["setup"]
        else:
            compact_source = source
            meta = source["meta"]
            loadout = source.get("loadout") or {}
            setup = _normalize_farm_setup(source.get("setup"))
        return {
            "id": publication["id"],
            "display_name": publication["display_name"],
            "family": str(meta.get("family") or "farm"),
            "tier": meta.get("tier"),
            "version": int(meta.get("version") or 1),
            "built_in": False,
            "editable": True,
            "published_at": publication["published_at"],
            "source_fingerprint": publication["source_fingerprint"],
            "plan_fingerprint": publication["plan_fingerprint"],
            "loadout": _copy_mapping(loadout),
            "setup": _copy_mapping(setup),
        }

    @staticmethod
    def _preset_catalogs(
        module_preset_catalog: Mapping[str, Any],
    ) -> dict[str, list[dict[str, str]]]:
        catalogs = {
            "modules": PROJECT_ROOT / "config" / "loadouts" / "modules.yaml",
            "orb_distance": (
                PROJECT_ROOT / "config" / "loadouts" / "orb_distances.yaml"
            ),
            "target_priority": (
                PROJECT_ROOT
                / "config"
                / "loadouts"
                / "target_priorities.yaml"
            ),
        }
        response: dict[str, list[dict[str, str]]] = {}
        for name, path in catalogs.items():
            if name == "modules":
                items = module_preset_catalog.get("items")
                if not isinstance(items, list):
                    raise StrategyProfileError(
                        "modules preset catalog has no items"
                    )
                response[name] = [
                    {
                        "id": str(item.get("id") or ""),
                        "display_name": str(item.get("display_name") or ""),
                    }
                    for item in items
                    if isinstance(item, Mapping)
                ]
                continue
            data = _load_yaml_mapping(path, f"{name} preset catalog")
            presets = data.get("presets")
            if not isinstance(presets, Mapping):
                raise StrategyProfileError(f"{name} preset catalog has no presets")
            response[name] = [
                {
                    "id": str(identifier),
                    "display_name": _default_display_name(str(identifier)),
                }
                for identifier in presets
            ]
        return response

    def _atomic_write(self, path: Path, publication: Mapping[str, Any]) -> None:
        self.profile_directory.mkdir(parents=True, exist_ok=True)
        temp_name: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.profile_directory,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
                yaml.safe_dump(
                    dict(publication),
                    handle,
                    sort_keys=False,
                    allow_unicode=True,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, path)
            temp_name = None
            self._fsync_directory(self.profile_directory)
        except OSError as exc:
            raise StrategyProfileError(
                f"Unable to publish strategy profile {path.name}: {exc}"
            ) from exc
        finally:
            if temp_name:
                try:
                    Path(temp_name).unlink(missing_ok=True)
                except OSError:
                    pass


def _draft_identifier(raw_profile: object) -> str:
    if not isinstance(raw_profile, Mapping):
        raise StrategyProfileError("profile must be an object")
    raw_identifier = raw_profile.get("id")
    identifier = normalize_strategy_id(raw_identifier)
    if identifier is None or str(raw_identifier or "").strip() != identifier:
        raise StrategyProfileError(
            "id must use 3-48 lowercase letters, digits, or underscores and start with a letter"
        )
    return identifier


def _normalize_policy(setting: str, raw: object) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(raw, Mapping):
        raise StrategyProfileError(f"loadout.{setting} must be an object")
    mode = str(raw.get("mode") or "").strip().lower()
    if mode not in POLICY_MODES:
        raise StrategyProfileError(
            f"loadout.{setting}.mode must be enforce, observe, or preserve"
        )
    return mode, raw


def _normalize_preset_policy(setting: str, raw: object) -> dict[str, str]:
    mode, policy = _normalize_policy(setting, raw)
    preset = str(policy.get("preset") or "").strip()
    if mode == "preserve":
        if preset:
            raise StrategyProfileError(
                f"loadout.{setting} preserve mode must not supply a preset"
            )
        return {"mode": mode}
    if not preset:
        raise StrategyProfileError(
            f"loadout.{setting} {mode} mode requires a preset"
        )
    return {"mode": mode, "preset": preset}


def _normalize_damage_policy(raw: object) -> dict[str, str]:
    mode, policy = _normalize_policy("damage_slider", raw)
    value = str(policy.get("value") or "").strip()
    if mode == "preserve":
        if value:
            raise StrategyProfileError(
                "loadout.damage_slider preserve mode must not supply a value"
            )
        return {"mode": mode}
    if not value:
        raise StrategyProfileError(
            f"loadout.damage_slider {mode} mode requires a value"
        )
    from core.damage_adjuster import normalize_damage_percentage

    try:
        value = normalize_damage_percentage(value)
    except ValueError as exc:
        raise StrategyProfileError(f"loadout.damage_slider {exc}") from exc
    return {"mode": mode, "value": value}


def _profile_path(directory: Path, identifier: str) -> Path:
    path = directory / f"{identifier}.profile.yaml"
    if path.parent != directory:
        raise StrategyProfileError("Invalid strategy profile path")
    return path


def _load_publication(path: Path, *, expected_id: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise StrategyProfileError(f"Profile publication is missing: {path}")
    loaded = _load_yaml_mapping_limited_profile(path, "Profile publication")
    return _validate_publication_document(loaded, expected_id=expected_id)


def _load_publication_unknown_id(
    path: Path,
    *,
    verify_builder: bool = True,
) -> dict[str, Any]:
    loaded = _load_yaml_mapping_limited_profile(path, "Profile publication")
    identifier = normalize_strategy_id(loaded.get("id"))
    if identifier is None or identifier in _RESERVED_STRATEGY_IDS:
        raise StrategyProfileError("Profile publication has an invalid Strategy id")
    return _validate_publication_document(
        loaded,
        expected_id=identifier,
        verify_builder=verify_builder,
    )


def _validate_publication_document(
    loaded: Mapping[str, Any],
    *,
    expected_id: str,
    verify_builder: bool = True,
) -> dict[str, Any]:
    if not isinstance(loaded, Mapping):
        raise StrategyProfileError("Profile publication must be an object")
    publication = _copy_mapping(loaded)
    schema_version = publication.get("schema_version")
    if schema_version not in {
        STRATEGY_PROFILE_SCHEMA_VERSION,
        STRATEGY_PUBLICATION_SCHEMA_VERSION,
    }:
        raise StrategyProfileError("Unsupported profile publication schema")
    identifier = normalize_strategy_id(publication.get("id"))
    if identifier != expected_id or identifier in _RESERVED_STRATEGY_IDS:
        raise StrategyProfileError("Profile publication id does not match its filename")
    if schema_version == STRATEGY_PUBLICATION_SCHEMA_VERSION:
        return _load_authoring_publication(publication, expected_id=expected_id)

    source = publication.get("source")
    plan = publication.get("plan")
    if not isinstance(source, dict) or not isinstance(plan, dict):
        raise StrategyProfileError("Profile publication requires source and plan objects")
    meta = source.get("meta")
    if (
        not isinstance(meta, Mapping)
        or meta.get("name") != identifier
        or str(meta.get("family") or "").strip().lower() != "farm"
        or source.get("builder") != "farm"
        or source.get("run_profile") != "farm"
    ):
        raise StrategyProfileError("Profile publication contains an invalid Farm source")
    source_fingerprint = str(publication.get("source_fingerprint") or "")
    plan_fingerprint = str(publication.get("plan_fingerprint") or "")
    if source_fingerprint != _fingerprint(source):
        raise StrategyProfileError("Profile source fingerprint does not match")
    if plan_fingerprint != _fingerprint(plan):
        raise StrategyProfileError("Profile plan fingerprint does not match")
    if verify_builder and build_strategy_yaml(source) != plan:
        raise StrategyProfileError("Profile plan is not the generated form of its source")
    display_name = str(publication.get("display_name") or "").strip()
    published_at = str(publication.get("published_at") or "").strip()
    if not display_name or len(display_name) > 80 or not published_at:
        raise StrategyProfileError("Profile publication metadata is incomplete")
    return publication


def _load_authoring_publication(
    loaded: dict[str, Any],
    *,
    expected_id: str,
) -> dict[str, Any]:
    if loaded.get("kind") != "strategy_publication":
        raise StrategyProfileError("Profile publication has the wrong kind")
    try:
        source = normalize_strategy_source(loaded.get("source"))
    except StrategyAuthoringError as exc:
        raise StrategyProfileError(f"Invalid authoring source: {exc}") from exc
    if source != loaded.get("source") or source["id"] != expected_id:
        raise StrategyProfileError("Profile publication contains a non-canonical source")

    base_snapshot = loaded.get("base_snapshot")
    base_resolution = loaded.get("base_resolution")
    stored_resolution = loaded.get("resolution")
    if not isinstance(stored_resolution, dict):
        raise StrategyProfileError("Profile publication requires a resolution")
    if source["schema_version"] == AUTHORING_SCHEMA_VERSION:
        if source.get("base") is not None and not isinstance(
            base_resolution,
            Mapping,
        ):
            raise StrategyProfileError(
                "Profile publication lacks its retained Base definition resolution"
            )
        if source.get("base") is None and base_resolution is not None:
            raise StrategyProfileError(
                "Profile publication without a Base has Base resolution data"
            )
        if source.get("base") is not None:
            try:
                canonical_base_resolution = describe_base_resolution(
                    base_snapshot,
                    base_resolution,
                )
            except StrategyAuthoringError as exc:
                raise StrategyProfileError(
                    f"Invalid embedded Base resolution: {exc}"
                ) from exc
            if canonical_base_resolution != base_resolution:
                raise StrategyProfileError(
                    "Profile Base resolution is not derived from its snapshot"
                )
    elif base_resolution is not None:
        raise StrategyProfileError(
            "Schema-2 authoring publication has unexpected Base resolution data"
        )
    try:
        resolution = resolve_strategy_source(
            source,
            base_snapshot,
            base_resolution_snapshot=base_resolution,
            retained_resolution=stored_resolution,
            require_base_definition_snapshots=(
                source["schema_version"] == AUTHORING_SCHEMA_VERSION
                and base_snapshot is not None
            ),
        )
    except StrategyAuthoringError as exc:
        raise StrategyProfileError(f"Invalid embedded base or resolution: {exc}") from exc
    if stored_resolution != resolution:
        raise StrategyProfileError("Profile resolution is not derived from its source")

    source_fingerprint = str(loaded.get("source_fingerprint") or "")
    base_fingerprint = str(loaded.get("base_fingerprint") or "")
    resolution_fingerprint = str(loaded.get("resolution_fingerprint") or "")
    plan_fingerprint = str(loaded.get("plan_fingerprint") or "")
    if source_fingerprint != fingerprint_document(source):
        raise StrategyProfileError("Profile source fingerprint does not match")
    if base_fingerprint != _base_state_fingerprint(
        base_snapshot,
        base_resolution,
        source_schema_version=source["schema_version"],
    ):
        raise StrategyProfileError("Profile base fingerprint does not match")
    if resolution_fingerprint != fingerprint_document(resolution):
        raise StrategyProfileError("Profile resolution fingerprint does not match")

    plan = loaded.get("plan")
    if not isinstance(plan, dict):
        raise StrategyProfileError("Profile publication requires a plan object")
    if plan_fingerprint != fingerprint_document(plan):
        raise StrategyProfileError("Profile plan fingerprint does not match")
    display_name = str(loaded.get("display_name") or "").strip()
    published_at = str(loaded.get("published_at") or "").strip()
    if (
        not display_name
        or display_name != source["display_name"]
        or len(display_name) > 80
        or not published_at
    ):
        raise StrategyProfileError("Profile publication metadata is incomplete")
    origin = loaded.get("publication_origin")
    audit_identity = loaded.get("audit_identity")
    if origin is not None or audit_identity is not None:
        if origin not in STRATEGY_PUBLICATION_ORIGINS:
            raise StrategyProfileError("Profile publication origin is invalid")
        _validate_audit_identity(audit_identity)
    return loaded


def _publication_version(publication: Mapping[str, Any]) -> int:
    source = publication["source"]
    if publication.get("schema_version") == STRATEGY_PUBLICATION_SCHEMA_VERSION:
        return int(source.get("version") or 0)
    meta = source.get("meta")
    if not isinstance(meta, Mapping):
        return 0
    return int(meta.get("version") or 0)


def _positive_version(value: object, description: str) -> int:
    if isinstance(value, bool):
        raise StrategyProfileError(f"{description} must be a positive integer")
    try:
        version = int(value)
    except (TypeError, ValueError) as exc:
        raise StrategyProfileError(
            f"{description} must be a positive integer"
        ) from exc
    if version < 1:
        raise StrategyProfileError(f"{description} must be a positive integer")
    return version


def _new_audit_identity() -> dict[str, str]:
    return {
        "authority": _AUDIT_AUTHORITY,
        "event_id": uuid.uuid4().hex,
    }


def _validate_audit_identity(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise StrategyProfileError("Strategy revision audit identity is invalid")
    if set(value) != {"authority", "event_id"}:
        raise StrategyProfileError("Strategy revision audit identity is invalid")
    authority = str(value.get("authority") or "")
    event_id = str(value.get("event_id") or "")
    if authority != _AUDIT_AUTHORITY or not _AUDIT_EVENT_ID_RE.fullmatch(event_id):
        raise StrategyProfileError("Strategy revision audit identity is invalid")
    return {"authority": authority, "event_id": event_id}


def _publication_projection(publication: Mapping[str, Any]) -> dict[str, Any]:
    identifier = normalize_strategy_id(publication.get("id"))
    if identifier is None:
        raise StrategyProfileError("Profile publication has an invalid Strategy id")
    validated = _validate_publication_document(
        publication,
        expected_id=identifier,
        verify_builder=False,
    )
    if validated["schema_version"] == STRATEGY_PUBLICATION_SCHEMA_VERSION:
        source = _copy_mapping(validated["source"])
        base_snapshot = _copy_optional_mapping(validated.get("base_snapshot"))
        base_resolution = _copy_optional_mapping(
            validated.get("base_resolution")
        )
        resolution = _copy_mapping(validated["resolution"])
    else:
        try:
            source = legacy_farm_source_to_strategy_source(
                validated["source"],
                display_name=validated["display_name"],
            )
            base_snapshot = None
            base_resolution = None
            resolution = resolve_strategy_source(source)
        except StrategyAuthoringError as exc:
            raise StrategyProfileError(
                f"Unable to project legacy Strategy source: {exc}"
            ) from exc
    plan = _copy_mapping(validated["plan"])
    return {
        "source": source,
        "base_snapshot": base_snapshot,
        "base_resolution": base_resolution,
        "resolution": resolution,
        "plan": plan,
        "source_fingerprint": str(validated["source_fingerprint"]),
        "normalized_source_fingerprint": fingerprint_document(source),
        "base_fingerprint": _base_state_fingerprint(
            base_snapshot,
            base_resolution,
            source_schema_version=source["schema_version"],
        ),
        "resolution_fingerprint": fingerprint_document(resolution),
        "plan_fingerprint": str(validated["plan_fingerprint"]),
        "rule_count": _plan_rule_count(plan),
    }


def _revision_envelope_from_publication(
    publication: Mapping[str, Any],
    *,
    origin: str,
    audit_identity: Mapping[str, Any],
) -> dict[str, Any]:
    if origin not in STRATEGY_PUBLICATION_ORIGINS:
        raise StrategyProfileError("Invalid Strategy publication origin")
    identity = _validate_audit_identity(audit_identity)
    projection = _publication_projection(publication)
    identifier = str(publication["id"])
    version = _publication_version(publication)
    if version < 1:
        raise StrategyProfileError("Strategy publication has no logical version")
    if _serialized_yaml_size(publication) > MAX_PROFILE_FILE_BYTES:
        raise StrategyProfileError(
            f"Profile publication exceeds {MAX_PROFILE_FILE_BYTES} bytes"
        )
    envelope = {
        "schema_version": STRATEGY_REVISION_SCHEMA_VERSION,
        "kind": "strategy_revision",
        "id": identifier,
        "display_name": str(publication["display_name"]),
        "logical_version": version,
        "published_at": str(publication["published_at"]),
        "publication_origin": origin,
        "audit_identity": identity,
        "publication_schema_version": int(publication["schema_version"]),
        "publication_fingerprint": fingerprint_document(publication),
        "source_fingerprint": projection["source_fingerprint"],
        "normalized_source_fingerprint": projection[
            "normalized_source_fingerprint"
        ],
        "base_fingerprint": projection["base_fingerprint"],
        "resolution_fingerprint": projection["resolution_fingerprint"],
        "plan_fingerprint": projection["plan_fingerprint"],
        "rule_count": projection["rule_count"],
        "source": projection["source"],
        "base_snapshot": projection["base_snapshot"],
        "base_resolution": projection["base_resolution"],
        "resolution": projection["resolution"],
        "plan": projection["plan"],
        "publication": _copy_mapping(publication),
    }
    if _serialized_yaml_size(envelope) > MAX_PROFILE_FILE_BYTES:
        raise StrategyProfileError(
            f"Strategy revision exceeds {MAX_PROFILE_FILE_BYTES} bytes"
        )
    return envelope


def _validate_revision_envelope(
    raw: Mapping[str, Any],
    *,
    expected_id: str,
    expected_version: int,
) -> dict[str, Any]:
    revision = _copy_mapping(raw)
    if revision.get("schema_version") != STRATEGY_REVISION_SCHEMA_VERSION:
        raise StrategyProfileError("Unsupported Strategy revision schema")
    if revision.get("kind") != "strategy_revision":
        raise StrategyProfileError("Stored Strategy revision has the wrong kind")
    if revision.get("id") != expected_id:
        raise StrategyProfileError("Stored Strategy revision id is invalid")
    if revision.get("logical_version") != expected_version:
        raise StrategyProfileError(
            "Stored Strategy logical version does not match its filename"
        )
    origin = revision.get("publication_origin")
    if origin not in STRATEGY_PUBLICATION_ORIGINS:
        raise StrategyProfileError("Stored Strategy publication origin is invalid")
    identity = _validate_audit_identity(revision.get("audit_identity"))
    publication = revision.get("publication")
    if not isinstance(publication, Mapping):
        raise StrategyProfileError("Strategy revision lacks its publication")
    projection = _publication_projection(publication)
    if publication.get("id") != expected_id:
        raise StrategyProfileError("Revision publication identity is invalid")
    if _publication_version(publication) != expected_version:
        raise StrategyProfileError("Revision publication version is invalid")
    if revision.get("display_name") != publication.get("display_name"):
        raise StrategyProfileError("Revision display name disagrees with publication")
    if revision.get("published_at") != publication.get("published_at"):
        raise StrategyProfileError("Revision timestamp disagrees with publication")
    if revision.get("publication_schema_version") != publication.get(
        "schema_version"
    ):
        raise StrategyProfileError("Revision publication schema is inconsistent")
    expected_fields = {
        "publication_fingerprint": fingerprint_document(publication),
        "source_fingerprint": projection["source_fingerprint"],
        "normalized_source_fingerprint": projection[
            "normalized_source_fingerprint"
        ],
        "base_fingerprint": projection["base_fingerprint"],
        "resolution_fingerprint": projection["resolution_fingerprint"],
        "plan_fingerprint": projection["plan_fingerprint"],
        "rule_count": projection["rule_count"],
        "source": projection["source"],
        "base_snapshot": projection["base_snapshot"],
        "base_resolution": projection["base_resolution"],
        "resolution": projection["resolution"],
        "plan": projection["plan"],
    }
    for field, expected in expected_fields.items():
        if revision.get(field) != expected:
            raise StrategyProfileError(
                f"Strategy revision {field} disagrees with its publication"
            )
    publication_origin = publication.get("publication_origin")
    publication_identity = publication.get("audit_identity")
    if (
        origin != "conservative_adoption"
        and publication_origin is not None
        and publication_origin != origin
    ):
        raise StrategyProfileError(
            "Revision origin disagrees with its latest-compatible publication"
        )
    if (
        origin != "conservative_adoption"
        and publication_identity is not None
        and publication_identity != identity
    ):
        raise StrategyProfileError(
            "Revision audit identity disagrees with its publication"
        )
    return revision


def _current_revision_validation(revision: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[str] = []
    try:
        source = normalize_strategy_source(revision.get("source"))
        base_snapshot = revision.get("base_snapshot")
        base_resolution = revision.get("base_resolution")
        resolution = resolve_strategy_source(
            source,
            base_snapshot,
            base_resolution_snapshot=base_resolution,
            retained_resolution=revision.get("resolution"),
            require_base_definition_snapshots=(
                source["schema_version"] == AUTHORING_SCHEMA_VERSION
                and base_snapshot is not None
            ),
        )
        if resolution != revision.get("resolution"):
            raise StrategyProfileError(
                "stored resolution differs from current resolver output"
            )
        compact = farm_source_from_resolution(source, resolution)
        plan = build_strategy_yaml(compact)
        if plan != revision.get("plan"):
            raise StrategyProfileError(
                "stored plan differs from current trusted builder output"
            )
    except Exception as exc:
        errors.append({"code": "current_validation", "message": str(exc)})
    return {"valid": not errors, "errors": errors, "warnings": warnings}


def _unvalidated_restore_candidate(
    revision: Mapping[str, Any],
    candidate_source: Mapping[str, Any],
) -> dict[str, Any]:
    """Project comparison data when current code cannot build a restore."""

    source = _copy_mapping(candidate_source)
    return {
        "source": source,
        "base_snapshot": _copy_optional_mapping(revision.get("base_snapshot")),
        "base_resolution": _copy_optional_mapping(
            revision.get("base_resolution")
        ),
        "resolution": _copy_mapping(revision.get("resolution")),
        "plan": _copy_mapping(revision.get("plan")),
        "profile": {
            "source_fingerprint": fingerprint_document(source),
            "base_fingerprint": str(revision.get("base_fingerprint") or ""),
            "resolution_fingerprint": str(
                revision.get("resolution_fingerprint") or ""
            ),
            "plan_fingerprint": str(revision.get("plan_fingerprint") or ""),
        },
        "rule_count": int(revision.get("rule_count") or 0),
    }


def _validate_transaction_record(
    raw: Mapping[str, Any],
    *,
    expected_id: str,
) -> dict[str, Any]:
    transaction = _copy_mapping(raw)
    if transaction.get("schema_version") != STRATEGY_TRANSACTION_SCHEMA_VERSION:
        raise StrategyProfileError("Unsupported Strategy transaction schema")
    if transaction.get("kind") != "strategy_publication_transaction":
        raise StrategyProfileError("Strategy transaction has the wrong kind")
    if transaction.get("id") != expected_id:
        raise StrategyProfileError("Strategy transaction identity is invalid")
    _positive_version(transaction.get("logical_version"), "logical_version")
    for field in ("publication_fingerprint", "revision_fingerprint"):
        if not _is_fingerprint(transaction.get(field)):
            raise StrategyProfileError(
                f"Strategy transaction {field} is invalid"
            )
    had_previous = transaction.get("had_previous_latest")
    previous = transaction.get("previous_publication_fingerprint")
    if not isinstance(had_previous, bool):
        raise StrategyProfileError("Strategy transaction previous state is invalid")
    if had_previous and not _is_fingerprint(previous):
        raise StrategyProfileError(
            "Strategy transaction previous fingerprint is invalid"
        )
    if not had_previous and previous is not None:
        raise StrategyProfileError(
            "Strategy transaction unexpectedly records a previous publication"
        )
    if not str(transaction.get("created_at") or "").strip():
        raise StrategyProfileError("Strategy transaction timestamp is missing")
    return transaction


def _plan_rule_count(plan: object) -> int:
    if not isinstance(plan, Mapping):
        return 0
    rules = plan.get("rules")
    return len(rules) if isinstance(rules, list) else 0


def _plans_equal_except_publication_metadata(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> bool:
    before_copy = _copy_mapping(before)
    after_copy = _copy_mapping(after)
    for plan in (before_copy, after_copy):
        meta = plan.get("meta")
        if isinstance(meta, dict):
            meta.pop("version", None)
    return before_copy == after_copy


def _filter_directive_changes(
    source_changes: Mapping[str, Any],
    *,
    policy: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {"added": [], "removed": [], "changed": []}

    def applies(value: object) -> bool:
        if not isinstance(value, Mapping):
            return False
        directive_policy = str(value.get("policy") or "")
        return (
            directive_policy == "ignore"
            if policy == "ignore"
            else bool(directive_policy and directive_policy != "ignore")
        )

    for category in result:
        for item in source_changes.get(category, []):
            if applies(item.get("before")) or applies(item.get("after")):
                result[category].append(_copy_mapping(item))
    result["change_count"] = sum(len(result[name]) for name in result)
    return result


def _copy_optional_mapping(value: object) -> Optional[dict[str, Any]]:
    return _copy_mapping(value) if isinstance(value, Mapping) else None


def _is_fingerprint(value: object) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", str(value or "")))


def _serialized_yaml_size(value: Mapping[str, Any]) -> int:
    return len(
        yaml.safe_dump(
            dict(value),
            sort_keys=False,
            allow_unicode=True,
        ).encode("utf-8")
    )


def _deduplicate_catalog_errors(
    errors: list[dict[str, str]],
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in errors:
        normalized = (str(item.get("id") or "history"), str(item.get("error") or ""))
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append({"id": normalized[0], "error": normalized[1]})
    return result


def _history_error_applies(identifier: str, error_id: str) -> bool:
    if error_id in {"history", "transactions", "retired"}:
        return True
    if error_id.startswith(("history/", "transactions/", "retired/")):
        return True
    return error_id == identifier or error_id.startswith(f"{identifier}@")


def _load_yaml_mapping_limited_profile(
    path: Path,
    description: str,
) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise StrategyProfileError(f"{description} is not a regular file")
        if path.stat().st_size > MAX_PROFILE_FILE_BYTES:
            raise StrategyProfileError(
                f"{description} exceeds {MAX_PROFILE_FILE_BYTES} bytes"
            )
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except StrategyProfileError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise StrategyProfileError(f"Unable to read {description}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise StrategyProfileError(f"{description} must be an object")
    return loaded


def _load_yaml_mapping(path: Path, description: str) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise StrategyProfileError(f"Unable to read {description}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise StrategyProfileError(f"{description} must be an object")
    return loaded


def _fingerprint(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _base_state_fingerprint(
    base_snapshot: object,
    base_resolution: object,
    *,
    source_schema_version: int,
) -> str:
    """Fingerprint the complete embedded Base state for each source schema."""

    if base_snapshot is None:
        return fingerprint_document({})
    if source_schema_version == LEGACY_AUTHORING_SCHEMA_VERSION:
        return fingerprint_document(base_snapshot)
    return fingerprint_document(
        {
            "source": base_snapshot,
            "resolution": base_resolution,
        }
    )


def _capture_semantic_value(setting_id: str, value: object) -> object:
    """Canonicalize only authoring values whose runtime order is irrelevant."""

    copied = json.loads(json.dumps(value, ensure_ascii=False))
    if setting_id in {"guardian_chips", "perk_bans"} and isinstance(
        copied,
        list,
    ):
        return sorted(copied)
    return copied


def _capture_effective_resolution_view(
    setting_id: str,
    entry: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare captured setup semantics without selector representation noise."""

    result = {
        key: _capture_semantic_value(setting_id, entry.get(key))
        for key in ("state", "policy")
        if key in entry
    }
    snapshot = entry.get("definition_snapshot")
    if isinstance(snapshot, Mapping) and "definition" in snapshot:
        # ``preset`` versus ``local`` and its fingerprint are provenance.  The
        # existing owner embeds the normalized definition that actually drives
        # runtime behavior, which is the setup value a capture review means.
        result["definition"] = _capture_semantic_value(
            setting_id,
            snapshot.get("definition"),
        )
    elif "value" in entry:
        result["value"] = _capture_semantic_value(
            setting_id,
            entry.get("value"),
        )
    return result


def _diff_captured_strategy_resolutions(
    before_resolution: object,
    after_resolution: object,
) -> dict[str, Any]:
    """Compare captured-versus-Base values using runtime-equivalent semantics."""

    if not isinstance(before_resolution, Mapping) or not isinstance(
        after_resolution,
        Mapping,
    ):
        raise StrategyProfileError("Capture comparison resolution is invalid")
    before_settings = before_resolution.get("settings")
    after_settings = after_resolution.get("settings")
    if not isinstance(before_settings, Mapping) or not isinstance(
        after_settings,
        Mapping,
    ):
        raise StrategyProfileError("Capture comparison settings are invalid")
    changed: list[dict[str, Any]] = []
    provenance_changed: list[dict[str, Any]] = []
    for setting_id, definition in FARM_SETTING_REGISTRY.items():
        before_entry = before_settings.get(setting_id)
        after_entry = after_settings.get(setting_id)
        if not isinstance(before_entry, Mapping) or not isinstance(
            after_entry,
            Mapping,
        ):
            raise StrategyProfileError(
                f"Capture comparison lacks setting {setting_id!r}"
            )
        item = {
            "setting_id": setting_id,
            "display_name": definition.display_name,
            "before": _copy_mapping(before_entry),
            "after": _copy_mapping(after_entry),
        }
        if _capture_effective_resolution_view(
            setting_id,
            before_entry,
        ) != _capture_effective_resolution_view(setting_id, after_entry):
            changed.append(item)
        elif before_entry != after_entry:
            provenance_changed.append(item)
    return {
        "changed": changed,
        "provenance_changed": provenance_changed,
        "change_count": len(changed),
    }


def _copy_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return json.loads(json.dumps(value, ensure_ascii=False))


def _copy_sequence_of_mappings(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(
        not isinstance(item, Mapping) for item in value
    ):
        raise StrategyProfileError("Capture unresolved review must be an array")
    return [_copy_mapping(item) for item in value]


def _normalize_farm_setup(raw: object) -> dict[str, Any]:
    """Return the editable Farm setup, inheriting omitted legacy values."""

    baseline = _load_yaml_mapping(FARM_RUN_PROFILE_PATH, "Farm run profile")
    invariants = baseline.get("invariants")
    if not isinstance(invariants, Mapping):
        raise StrategyProfileError("Farm run profile invariants must be an object")
    if raw is None:
        configured: Mapping[str, Any] = {}
    elif isinstance(raw, Mapping):
        configured = raw
    else:
        raise StrategyProfileError("setup must be an object")
    supported = {"skipped_checks", "settings"}
    extra = sorted(set(configured) - supported)
    if extra:
        raise StrategyProfileError(
            "setup has unsupported settings: "
            + ", ".join(str(key) for key in extra)
        )
    raw_settings = configured.get("settings")
    if raw_settings is None:
        raw_settings = {}
    if not isinstance(raw_settings, Mapping):
        raise StrategyProfileError("setup.settings must be an object")
    extra_settings = sorted(set(raw_settings) - set(invariants))
    if extra_settings:
        raise StrategyProfileError(
            "setup.settings has unsupported settings: "
            + ", ".join(str(key) for key in extra_settings)
        )
    requirements = json.loads(json.dumps(invariants, ensure_ascii=False))
    requirements.update(
        json.loads(json.dumps(dict(raw_settings), ensure_ascii=False))
    )
    try:
        first_choice = normalize_perk_first_choice_requirement(requirements)
        bans, auto_pick_order = normalize_perk_configuration_requirements(
            requirements
        )
        skipped = normalize_profile_skip_checks(
            configured.get("skipped_checks")
        )
    except ValueError as exc:
        raise StrategyProfileError(f"setup {exc}") from exc
    return {
        "skipped_checks": skipped,
        "settings": {
            **requirements,
            "perk_first_choice": first_choice,
            "perk_bans": bans,
            "perk_auto_pick_order": auto_pick_order,
        },
    }


def _default_display_name(identifier: str) -> str:
    special = {
        "farm_t18": "Farm T18",
        "farm_t19": "Farm T19",
        "tournament": "Tournament",
        "none": "No strategy",
    }
    if identifier in special:
        return special[identifier]
    return " ".join(part.upper() if part.startswith("t") and part[1:].isdigit() else part.capitalize() for part in identifier.split("_"))


def _profile_summary(source: Mapping[str, Any], rule_count: int) -> list[str]:
    loadout = source["loadout"]
    setup = source["setup"]
    summary = [
        f"Farm Tier {source['meta']['tier']} • generated {rule_count} rules",
    ]
    for setting in FARM_LOADOUT_KEYS:
        policy = loadout[setting]
        detail = policy.get("preset") or policy.get("value")
        label = setting.replace("_", " ").title()
        summary.append(
            f"{label}: {policy['mode']}"
            + (f" • {detail}" if detail else "")
        )
    skipped = setup["skipped_checks"]
    summary.append(
        "Permanent setup skips: "
        + (
            ", ".join(STARTUP_GATE_CHECK_LABELS[item] for item in skipped)
            if skipped
            else "none"
        )
    )
    settings = setup["settings"]
    summary.append(f"Perk Bans: {len(settings['perk_bans'])} selected")
    summary.append(
        f"Auto Pick priority: {len(settings['perk_auto_pick_order'])} ranked"
    )
    return summary


__all__ = [
    "BUILTIN_STRATEGY_IDS",
    "DEFAULT_CUSTOM_STRATEGY_DIR",
    "LEGACY_STRATEGY_ALIASES",
    "STRATEGY_AUTHORING_API_SCHEMA_VERSION",
    "STRATEGY_AUTHORING_OPERATIONS",
    "STRATEGY_HISTORY_API_SCHEMA_VERSION",
    "STRATEGY_PROFILE_DIRECTORY_ENVIRONMENT_VARIABLE",
    "STRATEGY_PUBLICATION_ORIGINS",
    "STRATEGY_REVISION_SCHEMA_VERSION",
    "StrategyProfileConflictError",
    "StrategyProfileError",
    "StrategyProfileStore",
    "canonical_strategy_id",
    "configurable_strategy_ids",
    "is_configurable_strategy",
    "load_published_strategy_plan",
    "normalize_strategy_id",
    "published_custom_strategy_ids",
    "strategy_profile_directory",
]
