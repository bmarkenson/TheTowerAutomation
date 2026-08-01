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
    ) -> None:
        self.profile_directory = strategy_profile_directory(profile_directory)
        self.builtin_strategies_directory = Path(
            builtin_strategies_directory
        ).resolve()
        self._publish_lock = threading.Lock()

    def strategy_ids(self) -> tuple[str, ...]:
        return configurable_strategy_ids(self.profile_directory)

    def has_strategy(self, value: object) -> bool:
        return is_configurable_strategy(
            value,
            self.profile_directory,
            allow_legacy_aliases=False,
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

        source, display_name = self._normalize_draft(raw_profile)
        try:
            plan = build_strategy_yaml(source)
        except Exception as exc:
            raise StrategyProfileError(str(exc)) from exc
        source_fingerprint = _fingerprint(source)
        plan_fingerprint = _fingerprint(plan)
        rules = plan.get("rules")
        rule_count = len(rules) if isinstance(rules, list) else 0
        loadout = source["loadout"]
        setup = source["setup"]
        return {
            "valid": True,
            "profile": {
                "id": source["meta"]["name"],
                "display_name": display_name,
                "family": "farm",
                "tier": source["meta"]["tier"],
                "version": source["meta"]["version"],
                "built_in": False,
                "editable": True,
                "published_at": None,
                "source_fingerprint": source_fingerprint,
                "plan_fingerprint": plan_fingerprint,
                "loadout": _copy_mapping(loadout),
                "setup": _copy_mapping(setup),
            },
            "source": source,
            "plan": plan,
            "rule_count": rule_count,
            "summary": _profile_summary(source, rule_count),
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
                    f"Profile {identifier!r} changed after it was opened; reload it before publishing"
                )
        elif expected is not None:
            raise StrategyProfileConflictError(
                f"Profile {identifier!r} no longer exists; reload the catalog"
            )

        validation = self.validate(raw_profile)
        source = validation.pop("source")
        plan = validation.pop("plan")
        publication = {
            "schema_version": STRATEGY_PROFILE_SCHEMA_VERSION,
            "id": identifier,
            "display_name": validation["profile"]["display_name"],
            "published_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "source_fingerprint": validation["profile"][
                "source_fingerprint"
            ],
            "plan_fingerprint": validation["profile"]["plan_fingerprint"],
            "source": source,
            "plan": plan,
        }
        self._atomic_write(path, publication)
        stored = _load_publication(path, expected_id=identifier)
        validation["profile"] = self._publication_item(stored)
        validation["published"] = True
        return validation

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

        path = _profile_path(self.profile_directory, identifier)
        version = 1
        if path.is_file() and not path.is_symlink():
            try:
                current = _load_publication(path, expected_id=identifier)
                current_version = int(current["source"]["meta"].get("version") or 0)
                version = current_version + 1
            except (StrategyProfileError, TypeError, ValueError):
                version = 1

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
        meta = source["meta"]
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
            "loadout": _copy_mapping(source.get("loadout") or {}),
            "setup": _normalize_farm_setup(source.get("setup")),
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
    if loaded.get("schema_version") != STRATEGY_PROFILE_SCHEMA_VERSION:
        raise StrategyProfileError("Unsupported profile publication schema")
    identifier = normalize_strategy_id(loaded.get("id"))
    if identifier != expected_id or identifier in _RESERVED_STRATEGY_IDS:
        raise StrategyProfileError("Profile publication id does not match its filename")
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
