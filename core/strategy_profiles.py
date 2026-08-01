"""Validated custom strategy profiles shared by the runtime and control surface."""

from __future__ import annotations

from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Mapping, Optional

import yaml

from core.gate_decisions import (
    PROFILE_SKIPPABLE_CHECKS,
    STARTUP_GATE_CHECK_LABELS,
    normalize_profile_skip_checks,
)
from core.perk_configuration import (
    PERK_CONFIGURATION_LABELS,
    normalize_perk_configuration_requirements,
)
from core.strategy_authoring import (
    AUTHORING_SCHEMA_VERSION,
    StrategyAuthoringConflictError,
    StrategyAuthoringError,
    StrategyBaseStore,
    analyze_strategy_source,
    describe_base_resolution,
    diff_source_documents,
    diff_strategy_resolutions,
    farm_source_from_resolution,
    fingerprint_document,
    legacy_farm_source_to_strategy_source,
    normalize_base_source,
    normalize_strategy_source,
    preview_strategy_rebase,
    rebase_review_fingerprint,
    resolve_strategy_source,
    setting_registry_catalog,
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
)
MAX_PROFILE_FILE_BYTES = 4 * 1024 * 1024
_STRATEGY_ID_RE = re.compile(r"[a-z][a-z0-9_]{2,47}")
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
    ) -> None:
        self.profile_directory = strategy_profile_directory(profile_directory)
        self.builtin_strategies_directory = Path(
            builtin_strategies_directory
        ).resolve()
        self.base_store = StrategyBaseStore(
            base_directory or self.profile_directory / "bases"
        )
        self._publish_lock = threading.Lock()

    def strategy_ids(self) -> tuple[str, ...]:
        return configurable_strategy_ids(self.profile_directory)

    def has_strategy(self, value: object) -> bool:
        return is_configurable_strategy(
            value,
            self.profile_directory,
            allow_legacy_aliases=False,
        )

    def setting_registry(self) -> list[dict[str, Any]]:
        """Return backend authoring metadata without exposing normalizers."""

        return setting_registry_catalog()

    def authoring_catalog(self) -> dict[str, Any]:
        """Return the additive Base/Strategy editor contract."""

        base_catalog = self.base_store.catalog()
        legacy_catalog = self.catalog()
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
        ]
        return {
            "schema_version": STRATEGY_AUTHORING_API_SCHEMA_VERSION,
            "setting_registry": self.setting_registry(),
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
            "catalog_errors": catalog_errors,
        }

    def validate_base(self, raw_base: object) -> dict[str, Any]:
        """Normalize a prospective next immutable Base revision."""

        try:
            initial = normalize_base_source(raw_base, revision=1)
            latest = self.base_store.latest(initial["id"])
            revision = (
                int(latest["snapshot"]["revision"]) + 1
                if latest is not None
                else 1
            )
            source = normalize_base_source(raw_base, revision=revision)
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
        source_fingerprint = fingerprint_document(source)
        return {
            "valid": True,
            "published": False,
            "source": source,
            "resolution": describe_base_resolution(source),
            "source_fingerprint": source_fingerprint,
            "expected_latest_fingerprint": (
                latest["source_fingerprint"] if latest is not None else None
            ),
            "fingerprints": {"source_fingerprint": source_fingerprint},
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
                "resolution": describe_base_resolution(
                    publication["snapshot"]
                ),
                "source_fingerprint": publication["source_fingerprint"],
                "published_at": publication["published_at"],
                "fingerprints": {
                    "source_fingerprint": publication["source_fingerprint"]
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
            before_resolution = analyze_strategy_source(before_source)[
                "resolution"
            ]
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
            proposed = normalize_strategy_source(raw_strategy)
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
            source = normalize_strategy_source(raw_strategy)
            current_snapshot = self._base_snapshot_for_source(source)
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

    def catalog(self) -> dict[str, Any]:
        """Return editor metadata without exposing expanded runtime plans."""

        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
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
            "presets": self._preset_catalogs(),
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
            base_snapshot = self._load_base_snapshot(source)
            resolution = resolve_strategy_source(source, base_snapshot)
            compact_source = farm_source_from_resolution(source, resolution)
            plan = build_strategy_yaml(compact_source)
        except Exception as exc:
            raise StrategyProfileError(str(exc)) from exc
        source_fingerprint = fingerprint_document(source)
        base_fingerprint = fingerprint_document(base_snapshot or {})
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
                "display_name": display_name,
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
    ) -> dict[str, Any]:
        """Atomically publish source and generated plan as one fixed file."""

        with self._publish_lock:
            return self._publish_locked(
                raw_profile,
                expected_source_fingerprint=expected_source_fingerprint,
            )

    def _publish_locked(
        self,
        raw_profile: object,
        *,
        expected_source_fingerprint: object,
    ) -> dict[str, Any]:
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
            return self._publish_under_file_lock(
                raw_profile,
                expected_source_fingerprint=expected_source_fingerprint,
            )

    def _publish_under_file_lock(
        self,
        raw_profile: object,
        *,
        expected_source_fingerprint: object,
    ) -> dict[str, Any]:
        identifier = _draft_identifier(raw_profile)
        if identifier in _RESERVED_STRATEGY_IDS:
            raise StrategyProfileError(
                f"{identifier!r} is a bundled or reserved strategy name"
            )
        path = _profile_path(self.profile_directory, identifier)
        existing: Optional[dict[str, Any]] = None
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise StrategyProfileConflictError(
                    f"Existing profile path for {identifier!r} is not a regular file"
                )
            try:
                existing = _load_publication(path, expected_id=identifier)
            except StrategyProfileError as exc:
                raise StrategyProfileConflictError(
                    f"Existing profile {identifier!r} is invalid and was preserved: {exc}"
                ) from exc

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

        validation = self.validate(raw_profile)
        source = validation.pop("source")
        base_snapshot = validation.pop("base_snapshot")
        resolution = validation.pop("resolution")
        plan = validation.pop("plan")
        publication = {
            "schema_version": STRATEGY_PUBLICATION_SCHEMA_VERSION,
            "kind": "strategy_publication",
            "id": identifier,
            "display_name": validation["profile"]["display_name"],
            "published_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "source_fingerprint": validation["profile"][
                "source_fingerprint"
            ],
            "base_fingerprint": validation["profile"]["base_fingerprint"],
            "resolution_fingerprint": validation["profile"][
                "resolution_fingerprint"
            ],
            "plan_fingerprint": validation["profile"]["plan_fingerprint"],
            "source": source,
            "base_snapshot": base_snapshot,
            "resolution": resolution,
            "plan": plan,
        }
        self._atomic_write(path, publication)
        stored = _load_publication(path, expected_id=identifier)
        validation["profile"] = self._publication_item(stored)
        validation["published"] = True
        return validation

    def _authoring_source_from_draft(
        self,
        raw_profile: object,
    ) -> tuple[dict[str, Any], str]:
        if not isinstance(raw_profile, Mapping):
            raise StrategyProfileError("profile must be an object")
        is_authoring = (
            raw_profile.get("kind") == "strategy"
            or raw_profile.get("schema_version") == AUTHORING_SCHEMA_VERSION
            and "settings" in raw_profile
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
                    raw_profile,
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

    def _load_base_snapshot(
        self,
        source: Mapping[str, Any],
    ) -> Optional[dict[str, Any]]:
        base = source.get("base")
        if not isinstance(base, Mapping):
            return None
        try:
            publication = self.base_store.load(base["id"], base["revision"])
        except StrategyAuthoringError as exc:
            raise StrategyProfileError(str(exc)) from exc
        return _copy_mapping(publication["snapshot"])

    def _base_snapshot_for_source(
        self,
        source: Mapping[str, Any],
    ) -> Optional[dict[str, Any]]:
        base = source.get("base")
        if not isinstance(base, Mapping):
            return None
        current = self._existing_authoring_state(str(source.get("id") or ""))
        if (
            current is not None
            and current["source"].get("base") == base
            and current["base_snapshot"] is not None
        ):
            return _copy_mapping(current["base_snapshot"])
        return self._load_base_snapshot(source)

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
        resolution = resolve_strategy_source(source, base_snapshot)
        return {
            "source": source,
            "base_snapshot": base_snapshot,
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
        path = _profile_path(self.profile_directory, identifier)
        if not path.is_file() or path.is_symlink():
            return 1
        try:
            publication = _load_publication(path, expected_id=identifier)
            return _publication_version(publication) + 1
        except (StrategyProfileError, TypeError, ValueError):
            return 1

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
    def _preset_catalogs() -> dict[str, list[dict[str, str]]]:
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
            directory_fd = os.open(self.profile_directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
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
    if not path.is_file():
        raise StrategyProfileError(f"Profile publication is missing: {path}")
    try:
        if path.stat().st_size > MAX_PROFILE_FILE_BYTES:
            raise StrategyProfileError(
                f"Profile publication exceeds {MAX_PROFILE_FILE_BYTES} bytes"
            )
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise StrategyProfileError(f"Unable to read profile publication: {exc}") from exc
    if not isinstance(loaded, dict):
        raise StrategyProfileError("Profile publication must be an object")
    schema_version = loaded.get("schema_version")
    if schema_version not in {
        STRATEGY_PROFILE_SCHEMA_VERSION,
        STRATEGY_PUBLICATION_SCHEMA_VERSION,
    }:
        raise StrategyProfileError("Unsupported profile publication schema")
    identifier = normalize_strategy_id(loaded.get("id"))
    if identifier != expected_id or identifier in _RESERVED_STRATEGY_IDS:
        raise StrategyProfileError("Profile publication id does not match its filename")
    if schema_version == STRATEGY_PUBLICATION_SCHEMA_VERSION:
        return _load_authoring_publication(loaded, expected_id=expected_id)

    source = loaded.get("source")
    plan = loaded.get("plan")
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
    source_fingerprint = str(loaded.get("source_fingerprint") or "")
    plan_fingerprint = str(loaded.get("plan_fingerprint") or "")
    if source_fingerprint != _fingerprint(source):
        raise StrategyProfileError("Profile source fingerprint does not match")
    if plan_fingerprint != _fingerprint(plan):
        raise StrategyProfileError("Profile plan fingerprint does not match")
    if build_strategy_yaml(source) != plan:
        raise StrategyProfileError("Profile plan is not the generated form of its source")
    display_name = str(loaded.get("display_name") or "").strip()
    published_at = str(loaded.get("published_at") or "").strip()
    if not display_name or len(display_name) > 80 or not published_at:
        raise StrategyProfileError("Profile publication metadata is incomplete")
    return loaded


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
    try:
        resolution = resolve_strategy_source(source, base_snapshot)
    except StrategyAuthoringError as exc:
        raise StrategyProfileError(f"Invalid embedded base or resolution: {exc}") from exc
    stored_resolution = loaded.get("resolution")
    if not isinstance(stored_resolution, dict) or stored_resolution != resolution:
        raise StrategyProfileError("Profile resolution is not derived from its source")

    source_fingerprint = str(loaded.get("source_fingerprint") or "")
    base_fingerprint = str(loaded.get("base_fingerprint") or "")
    resolution_fingerprint = str(loaded.get("resolution_fingerprint") or "")
    plan_fingerprint = str(loaded.get("plan_fingerprint") or "")
    if source_fingerprint != fingerprint_document(source):
        raise StrategyProfileError("Profile source fingerprint does not match")
    if base_fingerprint != fingerprint_document(base_snapshot or {}):
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
    return loaded


def _publication_version(publication: Mapping[str, Any]) -> int:
    source = publication["source"]
    if publication.get("schema_version") == STRATEGY_PUBLICATION_SCHEMA_VERSION:
        return int(source.get("version") or 0)
    meta = source.get("meta")
    if not isinstance(meta, Mapping):
        return 0
    return int(meta.get("version") or 0)


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


def _copy_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return json.loads(json.dumps(value, ensure_ascii=False))


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
    "STRATEGY_PROFILE_DIRECTORY_ENVIRONMENT_VARIABLE",
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
