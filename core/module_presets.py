"""Immutable bundled and installation-local custom Module preset catalog."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
from typing import Any, Callable, Iterator, Mapping, Optional

import yaml

from core.gc_module_loadout import normalize_gc_module_requirements
from core.module_icon_index import load_module_icon_catalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_MODULE_PRESETS_PATH = (
    PROJECT_ROOT / "config" / "loadouts" / "modules.yaml"
)
DEFAULT_CUSTOM_MODULE_PRESET_DIRECTORY = (
    PROJECT_ROOT / "config" / "loadouts" / "custom" / "modules"
)
MODULE_PRESET_SCHEMA_VERSION = 1
MODULE_PRESET_CATALOG_ID = "module_presets"
MAX_MODULE_PRESET_FILE_BYTES = 64 * 1024
_SAFE_ID_RE = re.compile(r"[a-z][a-z0-9_]{2,47}")
_CUSTOM_FILENAME_RE = re.compile(
    r"(?P<id>[a-z][a-z0-9_]{2,47})\.module-preset\.yaml"
)
_STAGE_FILENAME_RE = re.compile(
    r"\.(?P<id>[a-z][a-z0-9_]{2,47})\.module-preset\.stage\.yaml"
)


class ModulePresetError(ValueError):
    """A structured validation or storage failure for one Module preset."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "module_preset_validation",
        field: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


class ModulePresetConflictError(ModulePresetError):
    """A save-as-new request collided with immutable catalog state."""


class ModulePresetStore:
    """Merge immutable bundled presets with immutable custom preset files.

    The custom directory is server configuration. Callers supply only a safe
    preset ID, display name, and normalized definition; no API-facing path is
    accepted or returned.
    """

    def __init__(
        self,
        custom_directory: Path | str | None = None,
        *,
        bundled_path: Path | str = BUNDLED_MODULE_PRESETS_PATH,
        fault_hook: Optional[Callable[[str], None]] = None,
    ) -> None:
        selected_directory = (
            DEFAULT_CUSTOM_MODULE_PRESET_DIRECTORY
            if custom_directory is None
            else Path(custom_directory).expanduser()
        )
        self.custom_directory = Path(os.path.abspath(selected_directory))
        self.bundled_path = Path(
            os.path.abspath(Path(bundled_path).expanduser())
        )
        self._fault_hook = fault_hook
        self._thread_lock = threading.RLock()

    def catalog(self) -> dict[str, Any]:
        """Return one deterministic, path-free merged catalog snapshot."""

        bundled = self._bundled_items()
        try:
            with self._thread_lock:
                with self._catalog_lock():
                    recovery_errors = self._reconcile_stages_under_lock()
                    custom, custom_errors = self._custom_items_under_lock()
        except ModulePresetError as exc:
            return {
                "id": MODULE_PRESET_CATALOG_ID,
                "schema_version": MODULE_PRESET_SCHEMA_VERSION,
                "items": bundled,
                "errors": [
                    {
                        "id": "custom_modules",
                        "code": exc.code,
                        "error": str(exc),
                    }
                ],
            }
        return {
            "id": MODULE_PRESET_CATALOG_ID,
            "schema_version": MODULE_PRESET_SCHEMA_VERSION,
            "items": bundled + custom,
            "errors": recovery_errors + custom_errors,
        }

    def create(
        self,
        preset_id: object,
        display_name: object,
        definition: object,
    ) -> dict[str, Any]:
        """Durably create one immutable custom preset or reject the collision."""

        identifier = _safe_id(preset_id)
        name = _display_name(display_name, identifier)
        try:
            normalized = normalize_gc_module_requirements(definition)
        except (TypeError, ValueError) as exc:
            raise ModulePresetError(
                f"Module preset definition is invalid: {exc}",
                code="invalid_module_preset_definition",
                field="source",
            ) from exc

        bundled_ids = {item["id"] for item in self._bundled_items()}
        if identifier in bundled_ids:
            raise ModulePresetConflictError(
                f"Module preset ID {identifier!r} belongs to a bundled read-only preset",
                code="bundled_module_preset_collision",
                field="id",
            )

        payload = {
            "id": identifier,
            "display_name": name,
            "definition": normalized,
        }
        document = {
            "schema_version": MODULE_PRESET_SCHEMA_VERSION,
            "kind": "module_preset",
            **payload,
            "fingerprint": _fingerprint(payload),
        }

        with self._thread_lock:
            with self._catalog_lock():
                recovery_errors = self._reconcile_stages_under_lock()
                if recovery_errors:
                    raise ModulePresetConflictError(
                        "The custom Module preset catalog has unresolved staging "
                        "evidence; preserve it and repair the catalog before creating "
                        "another preset",
                        code="module_preset_catalog_conflict",
                    )
                path = self._preset_path(identifier)
                if path.exists() or path.is_symlink():
                    raise ModulePresetConflictError(
                        f"Module preset ID {identifier!r} already exists",
                        code="module_preset_id_collision",
                        field="id",
                    )
                self._atomic_create_under_lock(identifier, document)
                return self._load_custom_item(path, identifier)

    def definition(self, preset_id: object) -> dict[str, str]:
        """Resolve one ID through the same merged catalog used by the API."""

        identifier = _safe_id(preset_id)
        catalog = self.catalog()
        item = next(
            (candidate for candidate in catalog["items"] if candidate["id"] == identifier),
            None,
        )
        if item is None:
            raise ModulePresetError(
                f"Unknown Module preset {identifier!r}",
                code="unknown_module_preset",
                field="source",
            )
        return copy.deepcopy(item["definition"])

    def _bundled_items(self) -> list[dict[str, Any]]:
        if self.bundled_path.is_symlink():
            raise ModulePresetError(
                "The bundled Module preset catalog cannot be a symbolic link",
                code="invalid_bundled_module_preset_catalog",
            )
        raw = _read_yaml_mapping_no_follow(
            self.bundled_path,
            "bundled Module preset catalog",
        )
        if raw.get("schema_version") != MODULE_PRESET_SCHEMA_VERSION:
            raise ModulePresetError(
                "The bundled Module preset catalog has an unsupported schema",
                code="invalid_bundled_module_preset_catalog",
            )
        presets = raw.get("presets")
        if not isinstance(presets, Mapping) or not presets:
            raise ModulePresetError(
                "The bundled Module preset catalog requires presets",
                code="invalid_bundled_module_preset_catalog",
            )
        items: list[dict[str, Any]] = []
        for raw_id, raw_definition in presets.items():
            try:
                identifier = _safe_id(raw_id)
                if str(raw_id) != identifier:
                    raise ModulePresetError(
                        "Bundled Module preset IDs must already be canonical",
                        code="invalid_bundled_module_preset_catalog",
                    )
                definition = normalize_gc_module_requirements(raw_definition)
            except (ModulePresetError, TypeError, ValueError) as exc:
                raise ModulePresetError(
                    f"Bundled Module preset {raw_id!r} is invalid: {exc}",
                    code="invalid_bundled_module_preset_catalog",
                ) from exc
            items.append(
                _catalog_item(
                    identifier,
                    _display_name(None, identifier),
                    "bundled",
                    definition,
                )
            )
        return items

    @contextmanager
    def _catalog_lock(self) -> Iterator[None]:
        self._prepare_directory()
        lock_path = self.custom_directory / ".module-presets.write.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise ModulePresetError(
                "Unable to open the custom Module preset catalog lock: "
                + _os_error_text(exc),
                code="module_preset_store_unavailable",
            ) from exc
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ModulePresetConflictError(
                    "The custom Module preset catalog lock is not a regular file",
                    code="module_preset_catalog_conflict",
                )
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except OSError as exc:
                raise ModulePresetError(
                    "Unable to lock the custom Module preset catalog: "
                    + _os_error_text(exc),
                    code="module_preset_store_unavailable",
                ) from exc
            yield
        finally:
            os.close(descriptor)

    def _prepare_directory(self) -> None:
        if self.custom_directory.is_symlink() or (
            self.custom_directory.exists() and not self.custom_directory.is_dir()
        ):
            raise ModulePresetConflictError(
                "The custom Module preset catalog is not a regular directory",
                code="module_preset_catalog_conflict",
            )
        try:
            self.custom_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise ModulePresetError(
                "Unable to create the custom Module preset catalog: "
                + _os_error_text(exc),
                code="module_preset_store_unavailable",
            ) from exc

    def _custom_items_under_lock(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        try:
            paths = sorted(self.custom_directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise ModulePresetError(
                "Unable to enumerate the custom Module preset catalog: "
                + _os_error_text(exc),
                code="module_preset_store_unavailable",
            ) from exc

        bundled_ids = {item["id"] for item in self._bundled_items()}
        for path in paths:
            if path.name == ".module-presets.write.lock" or path.name.startswith("."):
                continue
            if not path.name.endswith(".yaml"):
                continue
            match = _CUSTOM_FILENAME_RE.fullmatch(path.name)
            if match is None:
                errors.append(
                    {
                        "id": "invalid_custom_entry",
                        "code": "invalid_module_preset_filename",
                        "error": "Invalid custom Module preset filename",
                    }
                )
                continue
            identifier = match.group("id")
            if identifier in bundled_ids:
                errors.append(
                    {
                        "id": identifier,
                        "code": "bundled_module_preset_collision",
                        "error": (
                            "A custom Module preset cannot shadow a bundled "
                            "read-only preset"
                        ),
                    }
                )
                continue
            try:
                items.append(self._load_custom_item(path, identifier))
            except ModulePresetError as exc:
                errors.append(
                    {
                        "id": identifier,
                        "code": exc.code,
                        "error": str(exc),
                    }
                )
        return sorted(items, key=lambda item: item["id"]), errors

    def _load_custom_item(self, path: Path, expected_id: str) -> dict[str, Any]:
        if path.is_symlink():
            raise ModulePresetError(
                "Symbolic-link custom Module presets are unsupported",
                code="invalid_custom_module_preset",
            )
        raw = _read_yaml_mapping_no_follow(path, "custom Module preset")
        expected_fields = {
            "schema_version",
            "kind",
            "id",
            "display_name",
            "definition",
            "fingerprint",
        }
        if set(raw) != expected_fields:
            raise ModulePresetError(
                "Custom Module preset has unsupported or missing fields",
                code="invalid_custom_module_preset",
            )
        if raw.get("schema_version") != MODULE_PRESET_SCHEMA_VERSION:
            raise ModulePresetError(
                "Custom Module preset has an unsupported schema",
                code="invalid_custom_module_preset",
            )
        if raw.get("kind") != "module_preset":
            raise ModulePresetError(
                "Custom Module preset has the wrong kind",
                code="invalid_custom_module_preset",
            )
        try:
            identifier = _safe_id(raw.get("id"))
            name = _display_name(raw.get("display_name"), identifier)
            definition = normalize_gc_module_requirements(raw.get("definition"))
        except (ModulePresetError, TypeError, ValueError) as exc:
            raise ModulePresetError(
                f"Custom Module preset content is invalid: {exc}",
                code="invalid_custom_module_preset",
            ) from exc
        if identifier != expected_id:
            raise ModulePresetError(
                "Custom Module preset identity does not match its fixed filename",
                code="invalid_custom_module_preset",
            )
        payload = {
            "id": identifier,
            "display_name": name,
            "definition": definition,
        }
        if raw.get("fingerprint") != _fingerprint(payload):
            raise ModulePresetError(
                "Custom Module preset fingerprint does not match",
                code="invalid_custom_module_preset",
            )
        canonical = {
            "schema_version": MODULE_PRESET_SCHEMA_VERSION,
            "kind": "module_preset",
            **payload,
            "fingerprint": raw["fingerprint"],
        }
        if canonical != raw:
            raise ModulePresetError(
                "Custom Module preset is not canonical",
                code="invalid_custom_module_preset",
            )
        return _catalog_item(identifier, name, "custom", definition)

    def _reconcile_stages_under_lock(self) -> list[dict[str, str]]:
        errors: list[dict[str, str]] = []
        changed = False
        for stage in sorted(self.custom_directory.glob(".*.stage.yaml")):
            match = _STAGE_FILENAME_RE.fullmatch(stage.name)
            if match is None:
                continue
            identifier = match.group("id")
            try:
                if stage.is_symlink():
                    raise ModulePresetConflictError(
                        "Symbolic-link Module preset stages are unsupported",
                        code="module_preset_catalog_conflict",
                    )
                staged = _read_yaml_mapping_no_follow(stage, "custom Module preset stage")
                self._validate_stage_document(staged, identifier)
                final = self._preset_path(identifier)
                if final.exists() or final.is_symlink():
                    if final.is_symlink():
                        raise ModulePresetConflictError(
                            "A staged Module preset conflicts with a symbolic-link target",
                            code="module_preset_catalog_conflict",
                        )
                    existing = _read_yaml_mapping_no_follow(
                        final,
                        "custom Module preset",
                    )
                    if existing != staged:
                        raise ModulePresetConflictError(
                            "A staged Module preset conflicts with different retained data",
                            code="module_preset_catalog_conflict",
                        )
                stage.unlink()
                changed = True
            except (ModulePresetError, OSError) as exc:
                errors.append(
                    {
                        "id": identifier,
                        "code": getattr(
                            exc,
                            "code",
                            "module_preset_catalog_conflict",
                        ),
                        "error": (
                            str(exc)
                            if isinstance(exc, ModulePresetError)
                            else "Unable to reconcile the custom Module preset stage: "
                            + _os_error_text(exc)
                        ),
                    }
                )
        if changed:
            _fsync_directory(self.custom_directory)
        return errors

    def _validate_stage_document(
        self,
        document: Mapping[str, Any],
        expected_id: str,
    ) -> None:
        # Reuse the complete custom-document validator without exposing a path.
        if document.get("id") != expected_id:
            raise ModulePresetConflictError(
                "Module preset stage identity is invalid",
                code="module_preset_catalog_conflict",
            )
        payload = {
            "id": document.get("id"),
            "display_name": document.get("display_name"),
            "definition": document.get("definition"),
        }
        expected = {
            "schema_version": MODULE_PRESET_SCHEMA_VERSION,
            "kind": "module_preset",
            **payload,
            "fingerprint": _fingerprint(payload),
        }
        if dict(document) != expected:
            raise ModulePresetConflictError(
                "Module preset stage is invalid",
                code="module_preset_catalog_conflict",
            )
        try:
            _safe_id(document.get("id"))
            _display_name(document.get("display_name"), expected_id)
            normalize_gc_module_requirements(document.get("definition"))
        except (ModulePresetError, TypeError, ValueError) as exc:
            raise ModulePresetConflictError(
                f"Module preset stage is invalid: {exc}",
                code="module_preset_catalog_conflict",
            ) from exc

    def _atomic_create_under_lock(
        self,
        identifier: str,
        document: Mapping[str, Any],
    ) -> None:
        stage = self._stage_path(identifier)
        final = self._preset_path(identifier)
        if stage.exists() or stage.is_symlink():
            raise ModulePresetConflictError(
                f"Module preset ID {identifier!r} has unresolved staging evidence",
                code="module_preset_catalog_conflict",
                field="id",
            )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        linked = False
        try:
            descriptor = os.open(stage, flags, 0o600)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    yaml.safe_dump(
                        dict(document),
                        handle,
                        sort_keys=False,
                        allow_unicode=True,
                    )
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                # fdopen owns and closes the descriptor after successful creation.
                raise
            self._fault("after_stage_fsync")
            try:
                os.link(stage, final, follow_symlinks=False)
            except FileExistsError as exc:
                raise ModulePresetConflictError(
                    f"Module preset ID {identifier!r} already exists",
                    code="module_preset_id_collision",
                    field="id",
                ) from exc
            linked = True
            self._fault("after_final_link")
            _fsync_directory(self.custom_directory)
            stage.unlink()
            _fsync_directory(self.custom_directory)
        except ModulePresetError:
            self._rollback_stage(stage, final, linked)
            raise
        except Exception as exc:
            self._rollback_stage(stage, final, linked)
            raise ModulePresetError(
                "Unable to create the custom Module preset: "
                + (
                    _os_error_text(exc)
                    if isinstance(exc, OSError)
                    else str(exc)
                ),
                code="module_preset_write_failed",
            ) from exc

    def _rollback_stage(self, stage: Path, final: Path, linked: bool) -> None:
        try:
            if linked and (final.exists() or final.is_symlink()):
                if not final.is_symlink() and stage.exists():
                    if os.path.samefile(stage, final):
                        final.unlink()
            if stage.exists() and not stage.is_symlink():
                stage.unlink()
            _fsync_directory(self.custom_directory)
        except OSError:
            # Reopen reconciliation will inspect any retained exact stage.
            pass

    def _fault(self, transition: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(transition)

    def _preset_path(self, identifier: str) -> Path:
        path = self.custom_directory / f"{identifier}.module-preset.yaml"
        if path.parent != self.custom_directory:
            raise ModulePresetError(
                "Invalid custom Module preset path",
                code="module_preset_catalog_conflict",
            )
        return path

    def _stage_path(self, identifier: str) -> Path:
        path = self.custom_directory / f".{identifier}.module-preset.stage.yaml"
        if path.parent != self.custom_directory:
            raise ModulePresetError(
                "Invalid custom Module preset stage",
                code="module_preset_catalog_conflict",
            )
        return path


def module_preset_definitions(catalog: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Project one catalog snapshot into resolver definitions."""

    items = catalog.get("items")
    if not isinstance(items, list):
        raise ModulePresetError("Module preset catalog requires items")
    return {
        str(item["id"]): copy.deepcopy(item["definition"])
        for item in items
        if isinstance(item, Mapping)
    }


def _catalog_item(
    identifier: str,
    display_name: str,
    origin: str,
    definition: Mapping[str, str],
) -> dict[str, Any]:
    catalog = load_module_icon_catalog()
    slots = [
        {
            "key": slot.key,
            "display_name": _title_identifier(slot.key),
            "family": slot.family,
            "role": slot.role,
            "module": definition[slot.key],
        }
        for slot in catalog.slots
    ]
    return {
        "id": identifier,
        "display_name": display_name,
        "origin": origin,
        "editable": False,
        "can_create_variant": True,
        "definition": copy.deepcopy(dict(definition)),
        "slots": slots,
    }


def _safe_id(value: object) -> str:
    supplied = str(value or "").strip()
    normalized = supplied.lower()
    if supplied != normalized or not _SAFE_ID_RE.fullmatch(normalized):
        raise ModulePresetError(
            "Module preset ID must use 3-48 lowercase letters, digits, or "
            "underscores and start with a letter",
            code="invalid_module_preset_id",
            field="id",
        )
    return normalized


def _display_name(value: object, identifier: str) -> str:
    display_name = str(value or "").strip() or _title_identifier(identifier)
    if len(display_name) > 80:
        raise ModulePresetError(
            "Module preset display name must be at most 80 characters",
            code="invalid_module_preset_display_name",
            field="display_name",
        )
    return display_name


def _title_identifier(value: object) -> str:
    return " ".join(
        word.capitalize() for word in str(value or "").replace("_", " ").split()
    )


def _fingerprint(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _read_yaml_mapping_no_follow(path: Path, description: str) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ModulePresetError(
            f"Unable to read {description}: {_os_error_text(exc)}",
            code="module_preset_store_unavailable",
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ModulePresetError(
                f"{description.capitalize()} is not a regular file",
                code="invalid_custom_module_preset",
            )
        if metadata.st_size > MAX_MODULE_PRESET_FILE_BYTES:
            raise ModulePresetError(
                f"{description.capitalize()} exceeds the bounded read limit",
                code="invalid_custom_module_preset",
            )
        chunks: list[bytes] = []
        remaining = MAX_MODULE_PRESET_FILE_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw_bytes = b"".join(chunks)
        if len(raw_bytes) > MAX_MODULE_PRESET_FILE_BYTES:
            raise ModulePresetError(
                f"{description.capitalize()} exceeds the bounded read limit",
                code="invalid_custom_module_preset",
            )
    finally:
        os.close(descriptor)
    try:
        loaded = yaml.safe_load(raw_bytes.decode("utf-8")) or {}
    except (UnicodeError, yaml.YAMLError) as exc:
        raise ModulePresetError(
            f"{description.capitalize()} is not valid UTF-8 YAML",
            code="invalid_custom_module_preset",
        ) from exc
    if not isinstance(loaded, dict):
        raise ModulePresetError(
            f"{description.capitalize()} must be an object",
            code="invalid_custom_module_preset",
        )
    return loaded


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _os_error_text(exc: OSError) -> str:
    return str(exc.strerror or exc.__class__.__name__)


__all__ = [
    "BUNDLED_MODULE_PRESETS_PATH",
    "DEFAULT_CUSTOM_MODULE_PRESET_DIRECTORY",
    "MAX_MODULE_PRESET_FILE_BYTES",
    "MODULE_PRESET_CATALOG_ID",
    "MODULE_PRESET_SCHEMA_VERSION",
    "ModulePresetConflictError",
    "ModulePresetError",
    "ModulePresetStore",
    "module_preset_definitions",
]
