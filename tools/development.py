#!/usr/bin/python3.12
"""Reproduce and validate an isolated TheTower development environment.

This module is deliberately standard-library-only so the configured host
interpreter can run ``bootstrap``, ``status``, ``verify-locks``, and ``lock``
before a worktree ``.venv`` exists.  ``checkpoint`` additionally requires the
published environment selected by the current worktree.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import csv
from dataclasses import dataclass
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import tomllib
from typing import Callable, Iterable, Iterator, Mapping, Sequence


CONFIG_RELATIVE_PATH = Path("requirements/development-environment.json")
MANIFEST_NAME = "THE_TOWER_ENVIRONMENT_MANIFEST.json"
WRITER_LOCK_NAME = "development-environment.write.lock"
LOCK_REGENERATION_COMMAND = ".venv/bin/python tools/development.py lock"
LOCK_SOURCES = {
    "requirements/bootstrap.lock": "requirements/bootstrap.in",
    "requirements/runtime.lock": "pyproject.toml (runtime + player-save)",
    "requirements/development.lock": "pyproject.toml (all optional groups)",
}
CHECKPOINT_TOOL_BLOCKLIST = ("adb", "ffmpeg", "scrcpy", "tesseract")
_LOCKED_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s;\\]+)(?:\s|$)"
)
_SHA256_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})(?:\s|$)")


class DevelopmentEnvironmentError(RuntimeError):
    """A fail-closed development bootstrap or checkpoint error."""


@dataclass(frozen=True)
class InterpreterIdentity:
    implementation: str
    version: str
    system: str
    machine: str
    platform_tag: str

    @property
    def major_minor(self) -> str:
        parts = self.version.split(".")
        if len(parts) < 2:
            raise DevelopmentEnvironmentError(
                f"Invalid configured Python version {self.version!r}"
            )
        return ".".join(parts[:2])


@dataclass(frozen=True)
class EnvironmentConfig:
    repository_root: Path
    config_path: Path
    bootstrap_schema: int
    dependency_inputs: tuple[str, ...]
    environment_root: Path
    production_environment: Path
    interpreter: Path
    supported_identity: InterpreterIdentity
    runtime_root: Path | None = None


@dataclass(frozen=True)
class EnvironmentFingerprint:
    digest: str
    input_hashes: Mapping[str, str]


@dataclass(frozen=True)
class ProvisionResult:
    environment: Path
    fingerprint: str
    reused: bool


@dataclass(frozen=True)
class CheckpointPaths:
    root: Path
    bytecode: Path
    pytest_cache: Path
    coverage_file: Path
    logs: Path
    screenshots: Path
    custom_configuration: Path
    scratch: Path
    blocked_tools: Path

    @classmethod
    def beneath(cls, root: Path) -> "CheckpointPaths":
        return cls(
            root=root,
            bytecode=root / "bytecode",
            pytest_cache=root / "pytest-cache",
            coverage_file=root / "coverage" / ".coverage",
            logs=root / "logs",
            screenshots=root / "screenshots",
            custom_configuration=root / "custom-configuration",
            scratch=root / "scratch",
            blocked_tools=root / "blocked-tools",
        )


def repository_root() -> Path:
    return Path(__file__).absolute().parent.parent


def load_config(
    root: Path,
    *,
    environment_root: Path | None = None,
    runtime_root: Path | None = None,
) -> EnvironmentConfig:
    root = Path(os.path.abspath(root))
    config_path = root / CONFIG_RELATIVE_PATH
    try:
        raw = json.loads(_read_regular_no_follow(config_path).decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DevelopmentEnvironmentError(
            f"Unable to read {CONFIG_RELATIVE_PATH}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise DevelopmentEnvironmentError(
            f"{CONFIG_RELATIVE_PATH} must contain a JSON object"
        )
    try:
        schema = int(raw["bootstrap_schema"])
        implementation = str(raw["implementation"])
        interpreter = Path(str(raw["interpreter"]))
        configured_environment_root = Path(str(raw["environment_root"]))
        production_environment = Path(str(raw["production_environment"]))
        platform_config = raw["platform"]
        dependency_inputs = tuple(str(item) for item in raw["dependency_inputs"])
        version = _read_regular_no_follow(root / ".python-version").decode(
            "utf-8"
        ).strip()
    except (KeyError, TypeError, ValueError, OSError, UnicodeError) as exc:
        raise DevelopmentEnvironmentError(
            f"{CONFIG_RELATIVE_PATH} is incomplete: {exc}"
        ) from exc
    if schema != 2:
        raise DevelopmentEnvironmentError(
            f"Unsupported development bootstrap schema {schema!r}"
        )
    if not isinstance(platform_config, dict):
        raise DevelopmentEnvironmentError("Configured platform must be an object")
    if not dependency_inputs or len(set(dependency_inputs)) != len(dependency_inputs):
        raise DevelopmentEnvironmentError(
            "Configured dependency inputs must be a non-empty unique list"
        )
    selected_environment_root = environment_root or configured_environment_root
    for description, value in (
        ("interpreter", interpreter),
        ("environment root", selected_environment_root),
        ("production environment", production_environment),
    ):
        if not value.is_absolute():
            raise DevelopmentEnvironmentError(
                f"Configured {description} must be an absolute path"
            )
    identity = InterpreterIdentity(
        implementation=implementation,
        version=version,
        system=str(platform_config.get("system") or ""),
        machine=str(platform_config.get("machine") or ""),
        platform_tag=str(platform_config.get("platform_tag") or ""),
    )
    config = EnvironmentConfig(
        repository_root=root,
        config_path=config_path,
        bootstrap_schema=schema,
        dependency_inputs=dependency_inputs,
        environment_root=Path(os.path.abspath(selected_environment_root)),
        production_environment=Path(os.path.abspath(production_environment)),
        interpreter=Path(os.path.abspath(interpreter)),
        supported_identity=identity,
        runtime_root=(
            Path(os.path.abspath(runtime_root)) if runtime_root is not None else None
        ),
    )
    validate_repository_root(config)
    return config


def validate_repository_root(config: EnvironmentConfig) -> None:
    root = config.repository_root
    development_root = config.environment_root.parent
    if _same_lexical_path(root, config.production_environment.parent):
        raise DevelopmentEnvironmentError(
            "The development bootstrap cannot run from the production checkout"
        )
    if not _path_is_within(root, development_root):
        raise DevelopmentEnvironmentError(
            f"Development worktree {root} is outside {development_root}"
        )
    if _path_is_within(root, config.environment_root):
        raise DevelopmentEnvironmentError(
            "The repository worktree cannot be inside the environment store"
        )


def current_interpreter_identity() -> InterpreterIdentity:
    return InterpreterIdentity(
        implementation=sys.implementation.name,
        version=platform.python_version(),
        system=platform.system(),
        machine=platform.machine(),
        platform_tag=sysconfig.get_platform(),
    )


def query_interpreter_identity(interpreter: Path) -> InterpreterIdentity:
    if not interpreter.is_absolute():
        raise DevelopmentEnvironmentError("Interpreter path must be absolute")
    try:
        details = interpreter.lstat()
    except OSError as exc:
        raise DevelopmentEnvironmentError(
            f"Configured interpreter {interpreter} is unavailable: {exc}"
        ) from exc
    if not stat.S_ISREG(details.st_mode) or interpreter.is_symlink():
        raise DevelopmentEnvironmentError(
            f"Configured interpreter {interpreter} must be a regular file"
        )
    program = (
        "import json,platform,sys,sysconfig;"
        "print(json.dumps({"
        "'implementation':sys.implementation.name,"
        "'version':platform.python_version(),"
        "'system':platform.system(),"
        "'machine':platform.machine(),"
        "'platform_tag':sysconfig.get_platform()"
        "},sort_keys=True))"
    )
    completed = _run_checked(
        [str(interpreter), "-I", "-c", program],
        description="configured interpreter identity check",
    )
    try:
        payload = json.loads(completed.stdout)
        return InterpreterIdentity(**payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DevelopmentEnvironmentError(
            "Configured interpreter returned invalid identity data"
        ) from exc


def require_supported_identity(
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    *,
    description: str = "interpreter",
) -> None:
    expected = config.supported_identity
    if identity != expected:
        raise DevelopmentEnvironmentError(
            f"Unsupported {description}: "
            f"{identity.implementation} {identity.version} "
            f"{identity.system}/{identity.machine} ({identity.platform_tag}); "
            f"required {expected.implementation} {expected.version} "
            f"{expected.system}/{expected.machine} ({expected.platform_tag})"
        )


def verify_interpreters(config: EnvironmentConfig) -> InterpreterIdentity:
    current = current_interpreter_identity()
    require_supported_identity(config, current, description="running interpreter")
    configured = query_interpreter_identity(config.interpreter)
    require_supported_identity(config, configured, description="configured interpreter")
    return configured


def compute_environment_fingerprint(
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
) -> EnvironmentFingerprint:
    require_supported_identity(config, identity)
    input_hashes: dict[str, str] = {}
    relative_paths = (CONFIG_RELATIVE_PATH.as_posix(), *config.dependency_inputs)
    for relative in sorted(relative_paths):
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise DevelopmentEnvironmentError(
                f"Dependency input path {relative!r} is not repository-relative"
            )
        payload = _read_regular_no_follow(config.repository_root / candidate)
        input_hashes[relative] = hashlib.sha256(payload).hexdigest()
    fingerprint_payload = {
        "bootstrap_schema": config.bootstrap_schema,
        "inputs": input_hashes,
        "interpreter": {
            "implementation": identity.implementation,
            "version": identity.version,
        },
        "platform": {
            "machine": identity.machine,
            "platform_tag": identity.platform_tag,
            "system": identity.system,
        },
    }
    canonical = json.dumps(
        fingerprint_payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return EnvironmentFingerprint(
        digest=hashlib.sha256(canonical).hexdigest(),
        input_hashes=input_hashes,
    )


def environment_name(identity: InterpreterIdentity, fingerprint: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        raise DevelopmentEnvironmentError("Environment fingerprint is not SHA-256")
    if re.fullmatch(r"[a-z0-9]+", identity.implementation) is None:
        raise DevelopmentEnvironmentError("Interpreter implementation is unsafe")
    return f"{identity.implementation}-{identity.major_minor}-{fingerprint}"


def expected_environment_path(
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: str,
) -> Path:
    return config.environment_root / environment_name(identity, fingerprint)


def validate_final_path(
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: str,
    final: Path,
) -> None:
    expected = expected_environment_path(config, identity, fingerprint)
    if not _same_lexical_path(final, expected):
        raise DevelopmentEnvironmentError(
            f"Environment path {final} does not match expected {expected}"
        )
    if _same_lexical_path(final, config.production_environment) or _path_is_within(
        final, config.production_environment
    ):
        raise DevelopmentEnvironmentError("Production .venv is never a development path")


def validate_stage_path(final: Path, stage: Path) -> None:
    expected_prefix = f".{final.name}.stage-"
    if not _same_lexical_path(stage.parent, final.parent):
        raise DevelopmentEnvironmentError("Environment stage is not a final-path sibling")
    if not stage.name.startswith(expected_prefix):
        raise DevelopmentEnvironmentError("Environment stage name is not owned by this build")
    try:
        details = stage.lstat()
    except OSError as exc:
        raise DevelopmentEnvironmentError(f"Environment stage is unavailable: {exc}") from exc
    if not stat.S_ISDIR(details.st_mode) or stage.is_symlink():
        raise DevelopmentEnvironmentError("Environment stage must be a no-follow directory")
    if details.st_uid != os.getuid():
        raise DevelopmentEnvironmentError("Environment stage has the wrong owner")


def parse_lock_file(path: Path) -> dict[str, str]:
    try:
        text = _read_regular_no_follow(path).decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise DevelopmentEnvironmentError(f"Unable to read lock {path}: {exc}") from exc
    logical_lines: list[str] = []
    pending = ""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        code = stripped
        if not pending and code.startswith("--"):
            raise DevelopmentEnvironmentError(f"Lock {path} contains an unpinned option")
        continuation = code.endswith("\\")
        if continuation:
            code = code[:-1].rstrip()
        pending = f"{pending} {code}".strip()
        if not continuation:
            logical_lines.append(pending)
            pending = ""
    if pending:
        raise DevelopmentEnvironmentError(f"Lock {path} has an incomplete continuation")
    requirements: dict[str, str] = {}
    for line in logical_lines:
        if line.startswith("#"):
            continue
        match = _LOCKED_REQUIREMENT.match(line)
        if match is None:
            raise DevelopmentEnvironmentError(
                f"Lock {path} contains a non-exact requirement: {line!r}"
            )
        name = canonical_distribution_name(match.group(1))
        version = match.group(2)
        hashes = _SHA256_HASH.findall(line)
        if not hashes:
            raise DevelopmentEnvironmentError(
                f"Lock {path} requirement {name} has no SHA-256 artifact hash"
            )
        if name in requirements:
            raise DevelopmentEnvironmentError(f"Lock {path} repeats {name}")
        requirements[name] = version
    if not requirements:
        raise DevelopmentEnvironmentError(f"Lock {path} has no requirements")
    return requirements


def validate_lock_inputs(config: EnvironmentConfig) -> dict[str, dict[str, str]]:
    locks: dict[str, dict[str, str]] = {}
    for relative, source in LOCK_SOURCES.items():
        path = config.repository_root / relative
        text = _read_regular_no_follow(path).decode("utf-8")
        if f"# Source declaration: {source}" not in text:
            raise DevelopmentEnvironmentError(
                f"Lock {relative} does not identify source {source}"
            )
        if f"# Regenerate: {LOCK_REGENERATION_COMMAND}" not in text:
            raise DevelopmentEnvironmentError(
                f"Lock {relative} does not identify its regeneration command"
            )
        locks[relative] = parse_lock_file(path)

    try:
        project = tomllib.loads(
            _read_regular_no_follow(config.repository_root / "pyproject.toml").decode(
                "utf-8"
            )
        )["project"]
        base = _direct_requirements(project["dependencies"])
        extras = project["optional-dependencies"]
        player_save = _direct_requirements(extras["player-save"])
        developer_tools = _direct_requirements(extras["developer-tools"])
        tests = _direct_requirements(extras["test"])
        bootstrap_direct = _requirements_from_input(
            config.repository_root / "requirements/bootstrap.in"
        )
    except (KeyError, TypeError, tomllib.TOMLDecodeError, UnicodeError) as exc:
        raise DevelopmentEnvironmentError(
            f"Canonical dependency declaration is invalid: {exc}"
        ) from exc

    runtime = locks["requirements/runtime.lock"]
    development = locks["requirements/development.lock"]
    bootstrap = locks["requirements/bootstrap.lock"]
    _require_locked_direct("runtime", runtime, {**base, **player_save})
    _require_locked_direct(
        "development",
        development,
        {**base, **player_save, **developer_tools, **tests},
    )
    _require_locked_direct("bootstrap", bootstrap, bootstrap_direct)
    for name, version in runtime.items():
        if development.get(name) != version:
            raise DevelopmentEnvironmentError(
                f"Development lock does not preserve runtime pin {name}=={version}"
            )
    return locks


def canonical_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _direct_requirements(items: object) -> dict[str, str]:
    if not isinstance(items, list):
        raise DevelopmentEnvironmentError("Dependency group must be a list")
    result: dict[str, str] = {}
    for item in items:
        if not isinstance(item, str):
            raise DevelopmentEnvironmentError("Dependency entries must be strings")
        match = _LOCKED_REQUIREMENT.match(item)
        if match is None or match.group(0) != item:
            raise DevelopmentEnvironmentError(
                f"Direct dependency must use an exact pin: {item!r}"
            )
        result[canonical_distribution_name(match.group(1))] = match.group(2)
    return result


def _requirements_from_input(path: Path) -> dict[str, str]:
    entries = []
    for line in _read_regular_no_follow(path).decode("utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return _direct_requirements(entries)


def _require_locked_direct(
    label: str,
    locked: Mapping[str, str],
    direct: Mapping[str, str],
) -> None:
    for name, version in direct.items():
        if locked.get(name) != version:
            raise DevelopmentEnvironmentError(
                f"{label.capitalize()} lock is missing {name}=={version}"
            )


def _runtime_root(config: EnvironmentConfig) -> Path:
    if config.runtime_root is not None:
        return config.runtime_root
    raw = os.environ.get("XDG_RUNTIME_DIR")
    if not raw:
        raise DevelopmentEnvironmentError(
            "XDG_RUNTIME_DIR is required for the host-global development writer lock"
        )
    base = Path(raw)
    if not base.is_absolute():
        raise DevelopmentEnvironmentError("XDG_RUNTIME_DIR must be absolute")
    return base / "thetower"


@contextmanager
def development_writer_lock(config: EnvironmentConfig) -> Iterator[None]:
    root = _runtime_root(config)
    _ensure_private_directory(root, create=True)
    lock_path = root / WRITER_LOCK_NAME
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise DevelopmentEnvironmentError(
            f"Unable to open development writer lock {lock_path}: {exc}"
        ) from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise DevelopmentEnvironmentError("Development writer lock is not regular")
        if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) & 0o077:
            raise DevelopmentEnvironmentError(
                "Development writer lock has unsafe ownership or permissions"
            )
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def provision_environment(
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: EnvironmentFingerprint,
    *,
    build_stage: Callable[
        [EnvironmentConfig, InterpreterIdentity, EnvironmentFingerprint, Path, Path],
        None,
    ]
    | None = None,
    verify_final: Callable[
        [Path, EnvironmentConfig, InterpreterIdentity, EnvironmentFingerprint], None
    ]
    | None = None,
) -> ProvisionResult:
    builder = build_stage or build_environment_stage
    verifier = verify_final or verify_published_environment
    final = expected_environment_path(config, identity, fingerprint.digest)
    validate_final_path(config, identity, fingerprint.digest, final)
    with development_writer_lock(config):
        _ensure_environment_root(config.environment_root)
        if os.path.lexists(final):
            verifier(final, config, identity, fingerprint)
            replace_worktree_environment_link(config, final)
            return ProvisionResult(final, fingerprint.digest, True)

        stage = _create_stage_directory(config.environment_root, final)
        published = False
        try:
            validate_stage_path(final, stage)
            builder(config, identity, fingerprint, stage, final)
            verify_environment_manifest(
                stage,
                expected_final=final,
                config=config,
                identity=identity,
                fingerprint=fingerprint,
            )
            _sync_tree(stage)
            _publish_stage(config.environment_root, stage, final)
            published = True
        except Exception:
            if not published:
                cleanup_owned_stage(stage, final)
            raise
        verifier(final, config, identity, fingerprint)
        replace_worktree_environment_link(config, final)
        return ProvisionResult(final, fingerprint.digest, False)


def build_environment_stage(
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: EnvironmentFingerprint,
    stage: Path,
    final: Path,
) -> None:
    require_supported_identity(config, identity)
    validate_stage_path(final, stage)
    environment = _isolated_subprocess_environment(stage / ".artifact-cache")
    _run_checked(
        [str(config.interpreter), "-I", "-m", "venv", str(stage)],
        description="development virtual-environment creation",
        env=environment,
    )
    stage_python = stage / "bin/python"
    _run_checked(
        [
            str(stage_python),
            "-I",
            "-m",
            "pip",
            "install",
            "--require-hashes",
            "--only-binary=:all:",
            "--no-deps",
            "--no-compile",
            "--force-reinstall",
            "--no-input",
            "-r",
            str(config.repository_root / "requirements/bootstrap.lock"),
        ],
        description="hash-verified bootstrap toolchain installation",
        env=environment,
    )
    _run_checked(
        [
            str(stage_python),
            "-I",
            "-m",
            "pip",
            "install",
            "--require-hashes",
            "--no-deps",
            "--no-compile",
            "--no-build-isolation",
            "--no-input",
            "-r",
            str(config.repository_root / "requirements/development.lock"),
        ],
        description="hash-verified development dependency installation",
        env=environment,
    )
    _run_checked(
        [str(stage_python), "-I", "-m", "pip", "check"],
        description="installed dependency consistency check",
        env=environment,
    )
    cache = stage / ".artifact-cache"
    if cache.exists():
        shutil.rmtree(cache)
    remove_generated_bytecode(stage)
    normalize_relocated_environment(stage, final)
    expected = _expected_installed_distributions(config)
    _verify_environment_python(
        stage,
        expected_prefix=stage,
        identity=identity,
        expected_distributions=expected,
        execute_console_script=False,
    )
    _make_contents_immutable(stage)
    manifest = _environment_manifest(
        stage,
        final=final,
        config=config,
        identity=identity,
        fingerprint=fingerprint,
    )
    manifest_path = stage / MANIFEST_NAME
    _write_json_no_follow(manifest_path, manifest)
    os.chmod(manifest_path, 0o444)
    os.chmod(stage, 0o555)


def remove_generated_bytecode(root: Path) -> None:
    directories: list[Path] = []
    for relative, details in _iter_tree(root):
        path = root / relative
        if stat.S_ISDIR(details.st_mode) and path.name == "__pycache__":
            directories.append(path)
        elif stat.S_ISREG(details.st_mode) and path.suffix in {".pyc", ".pyo"}:
            path.unlink()
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        if directory.exists() and not directory.is_symlink():
            shutil.rmtree(directory)


def normalize_relocated_environment(stage: Path, final: Path) -> None:
    stage_bytes = os.fsencode(str(stage))
    final_bytes = os.fsencode(str(final))
    for relative, details in list(_iter_tree(stage)):
        path = stage / relative
        if stat.S_ISLNK(details.st_mode):
            target = os.readlink(path)
            if str(stage) in target:
                replacement = target.replace(str(stage), str(final))
                path.unlink()
                os.symlink(replacement, path)
            continue
        if not stat.S_ISREG(details.st_mode):
            continue
        if not _file_contains(path, stage_bytes):
            continue
        payload = _read_regular_no_follow(path)
        if b"\x00" in payload:
            raise DevelopmentEnvironmentError(
                f"Unsupported binary staging-prefix reference in {relative}"
            )
        path.write_bytes(payload.replace(stage_bytes, final_bytes))
    _rewrite_distribution_records(stage)
    for relative, details in _iter_tree(stage):
        path = stage / relative
        if stat.S_ISLNK(details.st_mode):
            if str(stage) in os.readlink(path):
                raise DevelopmentEnvironmentError(
                    f"Staging prefix remains in symlink {relative}"
                )
        elif stat.S_ISREG(details.st_mode) and _file_contains(path, stage_bytes):
            raise DevelopmentEnvironmentError(
                f"Staging prefix remains in environment file {relative}"
            )
    _validate_console_script_shebangs(stage, final)


def _rewrite_distribution_records(root: Path) -> None:
    records = [
        root / relative
        for relative, details in _iter_tree(root)
        if stat.S_ISREG(details.st_mode)
        and Path(relative).name == "RECORD"
        and Path(relative).parent.name.endswith(".dist-info")
    ]
    for record in records:
        rows: list[list[str]] = []
        with record.open("r", encoding="utf-8", newline="") as handle:
            source_rows = list(csv.reader(handle))
        for row in source_rows:
            if not row:
                continue
            # PEP 376 RECORD paths are relative to the installation root
            # containing the ``*.dist-info`` directory, not to that directory.
            candidate = Path(os.path.abspath(record.parent.parent / row[0]))
            if not _path_is_within(candidate, root):
                raise DevelopmentEnvironmentError(
                    f"Distribution RECORD escapes environment: {row[0]!r}"
                )
            if _same_lexical_path(candidate, record):
                _require_no_symlink_ancestors(candidate, root)
                rows.append([row[0], "", ""])
                continue
            try:
                details = candidate.lstat()
            except FileNotFoundError:
                continue
            _require_no_symlink_ancestors(candidate, root)
            if not stat.S_ISREG(details.st_mode):
                raise DevelopmentEnvironmentError(
                    f"Distribution RECORD target is not regular: {row[0]!r}"
                )
            payload = _read_regular_no_follow(candidate)
            digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(
                b"="
            )
            rows.append([row[0], f"sha256={digest.decode('ascii')}", str(len(payload))])
        rendered = io.StringIO(newline="")
        writer = csv.writer(rendered, lineterminator="\n")
        writer.writerows(rows)
        temporary = record.with_name(".RECORD.thetower-stage")
        if os.path.lexists(temporary):
            raise DevelopmentEnvironmentError(
                f"Unexpected RECORD stage already exists: {temporary}"
            )
        temporary.write_text(rendered.getvalue(), encoding="utf-8")
        os.replace(temporary, record)


def _validate_console_script_shebangs(root: Path, final: Path) -> None:
    bin_directory = root / "bin"
    for entry in sorted(os.scandir(bin_directory), key=lambda item: item.name):
        details = entry.stat(follow_symlinks=False)
        if not stat.S_ISREG(details.st_mode):
            continue
        with open(entry.path, "rb") as handle:
            first_line = handle.readline(4096).rstrip(b"\r\n")
        if first_line.startswith(b"#!") and b"python" in first_line.lower():
            expected_prefix = os.fsencode(f"#!{final}/bin/python")
            if not first_line.startswith(expected_prefix):
                raise DevelopmentEnvironmentError(
                    f"Console script {entry.name} does not target the final environment"
                )


def _environment_manifest(
    root: Path,
    *,
    final: Path,
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: EnvironmentFingerprint,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "bootstrap_schema": config.bootstrap_schema,
        "environment_fingerprint": fingerprint.digest,
        "environment_path": str(final),
        "input_hashes": dict(sorted(fingerprint.input_hashes.items())),
        "interpreter": {
            "implementation": identity.implementation,
            "version": identity.version,
        },
        "platform": {
            "machine": identity.machine,
            "platform_tag": identity.platform_tag,
            "system": identity.system,
        },
        "relocation": _relocation_contract(final),
        "files": _inventory_tree(root),
    }


def _inventory_tree(root: Path) -> list[dict[str, object]]:
    inventory: list[dict[str, object]] = []
    for relative, details in _iter_tree(root):
        if relative.as_posix() == MANIFEST_NAME:
            continue
        item: dict[str, object] = {
            "path": relative.as_posix(),
            "owner": details.st_uid,
        }
        mode = stat.S_IMODE(details.st_mode)
        if stat.S_ISDIR(details.st_mode):
            item.update(type="directory", mode=mode)
        elif stat.S_ISREG(details.st_mode):
            payload = _read_regular_no_follow(root / relative)
            item.update(
                type="file",
                mode=mode,
                size=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        elif stat.S_ISLNK(details.st_mode):
            item.update(type="symlink", target=os.readlink(root / relative))
        else:
            raise DevelopmentEnvironmentError(
                f"Unsupported environment entry type: {relative}"
            )
        inventory.append(item)
    inventory.sort(key=lambda item: str(item["path"]))
    return inventory


def verify_environment_manifest(
    actual_root: Path,
    *,
    expected_final: Path,
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: EnvironmentFingerprint,
) -> None:
    try:
        root_details = actual_root.lstat()
    except OSError as exc:
        raise DevelopmentEnvironmentError(
            f"Environment {actual_root} is unavailable: {exc}"
        ) from exc
    if not stat.S_ISDIR(root_details.st_mode) or actual_root.is_symlink():
        raise DevelopmentEnvironmentError("Environment root must be a no-follow directory")
    if root_details.st_uid != os.getuid():
        raise DevelopmentEnvironmentError("Environment root has the wrong owner")
    if stat.S_IMODE(root_details.st_mode) & 0o222:
        raise DevelopmentEnvironmentError("Published environment root is writable")
    manifest_path = actual_root / MANIFEST_NAME
    try:
        manifest_details = manifest_path.lstat()
        manifest = json.loads(_read_regular_no_follow(manifest_path).decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DevelopmentEnvironmentError(f"Environment manifest is unreadable: {exc}") from exc
    if (
        not stat.S_ISREG(manifest_details.st_mode)
        or manifest_details.st_uid != os.getuid()
        or stat.S_IMODE(manifest_details.st_mode) & 0o222
    ):
        raise DevelopmentEnvironmentError(
            "Environment manifest has unsafe type, owner, or permissions"
        )
    expected_fields = {
        "schema_version": 1,
        "bootstrap_schema": config.bootstrap_schema,
        "environment_fingerprint": fingerprint.digest,
        "environment_path": str(expected_final),
        "input_hashes": dict(sorted(fingerprint.input_hashes.items())),
        "interpreter": {
            "implementation": identity.implementation,
            "version": identity.version,
        },
        "platform": {
            "machine": identity.machine,
            "platform_tag": identity.platform_tag,
            "system": identity.system,
        },
        "relocation": _relocation_contract(expected_final),
    }
    if not isinstance(manifest, dict):
        raise DevelopmentEnvironmentError("Environment manifest is not an object")
    for key, expected in expected_fields.items():
        if manifest.get(key) != expected:
            raise DevelopmentEnvironmentError(
                f"Environment manifest {key} does not match the lock fingerprint"
            )
    actual_inventory = _inventory_tree(actual_root)
    if manifest.get("files") != actual_inventory:
        raise DevelopmentEnvironmentError("Environment installed-file manifest mismatch")
    for item in actual_inventory:
        relative = Path(str(item["path"]))
        if int(item["owner"]) != os.getuid():
            raise DevelopmentEnvironmentError(
                f"Environment entry has the wrong owner: {item['path']}"
            )
        if item["type"] in {"directory", "file"} and int(item["mode"]) & 0o222:
            raise DevelopmentEnvironmentError(
                f"Environment entry is writable: {item['path']}"
            )
        if item["type"] == "symlink":
            _validate_environment_symlink(actual_root, item)
        if relative.name == "__pycache__" or relative.suffix in {".pyc", ".pyo"}:
            raise DevelopmentEnvironmentError(
                f"Published environment contains generated bytecode: {relative}"
            )
    _verify_no_staging_prefix(actual_root, expected_final)


def _relocation_contract(final: Path) -> dict[str, str]:
    return {
        "final_prefix": str(final),
        "forbidden_stage_prefix": str(final.parent / f".{final.name}.stage-"),
    }


def _verify_no_staging_prefix(root: Path, final: Path) -> None:
    forbidden = os.fsencode(_relocation_contract(final)["forbidden_stage_prefix"])
    forbidden_text = os.fsdecode(forbidden)
    for relative, details in _iter_tree(root):
        path = root / relative
        if relative.as_posix() == MANIFEST_NAME:
            continue
        if stat.S_ISLNK(details.st_mode):
            if forbidden_text in os.readlink(path):
                raise DevelopmentEnvironmentError(
                    f"Published symlink retains a staging prefix: {relative}"
                )
        elif stat.S_ISREG(details.st_mode) and _file_contains(path, forbidden):
            raise DevelopmentEnvironmentError(
                f"Published file retains a staging prefix: {relative}"
            )


def verify_published_environment(
    final: Path,
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: EnvironmentFingerprint,
) -> None:
    validate_final_path(config, identity, fingerprint.digest, final)
    verify_environment_manifest(
        final,
        expected_final=final,
        config=config,
        identity=identity,
        fingerprint=fingerprint,
    )
    expected = _expected_installed_distributions(config)
    _verify_environment_python(
        final,
        expected_prefix=final,
        identity=identity,
        expected_distributions=expected,
        execute_console_script=True,
    )


def _expected_installed_distributions(config: EnvironmentConfig) -> dict[str, str]:
    locks = validate_lock_inputs(config)
    expected = dict(locks["requirements/bootstrap.lock"])
    for name, version in locks["requirements/development.lock"].items():
        previous = expected.get(name)
        if previous is not None and previous != version:
            raise DevelopmentEnvironmentError(
                f"Bootstrap/development locks conflict for {name}"
            )
        expected[name] = version
    return expected


def _verify_environment_python(
    root: Path,
    *,
    expected_prefix: Path,
    identity: InterpreterIdentity,
    expected_distributions: Mapping[str, str],
    execute_console_script: bool,
) -> None:
    program = (
        "import importlib.metadata,json,os,platform,sys,sysconfig;"
        "installed={};"
        "[(installed.setdefault(re.sub(r'[-_.]+','-',d.metadata['Name']).lower(),d.version)) "
        "for d in importlib.metadata.distributions() if d.metadata.get('Name')];"
        "print(json.dumps({'prefix':os.path.realpath(sys.prefix),"
        "'implementation':sys.implementation.name,'version':platform.python_version(),"
        "'system':platform.system(),'machine':platform.machine(),"
        "'platform_tag':sysconfig.get_platform(),'installed':installed},sort_keys=True))"
    )
    # Keep the one-line verifier self-contained without importing repository code.
    program = "import re;" + program
    with tempfile.TemporaryDirectory(prefix="thetower-environment-check-") as cache:
        environment = _isolated_subprocess_environment(Path(cache))
        completed = _run_checked(
            [str(root / "bin/python"), "-I", "-B", "-c", program],
            description="published environment interpreter check",
            env=environment,
        )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise DevelopmentEnvironmentError(
                "Published environment returned invalid identity data"
            ) from exc
        observed = InterpreterIdentity(
            implementation=str(payload.get("implementation")),
            version=str(payload.get("version")),
            system=str(payload.get("system")),
            machine=str(payload.get("machine")),
            platform_tag=str(payload.get("platform_tag")),
        )
        if observed != identity:
            raise DevelopmentEnvironmentError("Published environment identity mismatch")
        if payload.get("prefix") != os.path.realpath(expected_prefix):
            raise DevelopmentEnvironmentError(
                "Published environment interpreter resolves to the wrong prefix"
            )
        if payload.get("installed") != dict(sorted(expected_distributions.items())):
            raise DevelopmentEnvironmentError(
                "Published environment distribution set does not match the locks"
            )
        _run_checked(
            [
                str(root / "bin/python"),
                "-I",
                "-B",
                "-m",
                "pytest",
                "--version",
            ],
            description="published pytest module check",
            env=environment,
        )
        if execute_console_script:
            _run_checked(
                [str(root / "bin/pytest"), "--version"],
                description="relocated pytest console-script check",
                env=environment,
            )


def replace_worktree_environment_link(config: EnvironmentConfig, final: Path) -> None:
    link = config.repository_root / ".venv"
    if os.path.lexists(link) and not link.is_symlink():
        raise DevelopmentEnvironmentError(
            f"Refusing to replace non-symlink worktree environment {link}"
        )
    temporary = config.repository_root / f".venv.link-{secrets.token_hex(8)}"
    if os.path.lexists(temporary):
        raise DevelopmentEnvironmentError("Unexpected temporary .venv link collision")
    try:
        os.symlink(str(final), temporary, target_is_directory=True)
        os.replace(temporary, link)
        _fsync_directory(config.repository_root)
    finally:
        if os.path.lexists(temporary):
            temporary.unlink()
    verify_worktree_environment_link(config, final)


def verify_worktree_environment_link(config: EnvironmentConfig, final: Path) -> None:
    link = config.repository_root / ".venv"
    try:
        details = link.lstat()
    except OSError as exc:
        raise DevelopmentEnvironmentError(f"Worktree .venv link is unavailable: {exc}") from exc
    if not stat.S_ISLNK(details.st_mode):
        raise DevelopmentEnvironmentError("Worktree .venv must be a symlink")
    target = os.readlink(link)
    if not os.path.isabs(target) or not _same_lexical_path(Path(target), final):
        raise DevelopmentEnvironmentError(
            f"Worktree .venv targets {target!r}, expected {final}"
        )
    if _same_lexical_path(Path(target), config.production_environment):
        raise DevelopmentEnvironmentError("Worktree .venv points at production")


def require_active_development_environment(
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: EnvironmentFingerprint,
) -> Path:
    final = expected_environment_path(config, identity, fingerprint.digest)
    verify_worktree_environment_link(config, final)
    running_prefix = Path(os.path.realpath(sys.prefix))
    if _same_lexical_path(running_prefix, config.production_environment):
        raise DevelopmentEnvironmentError("Checkpoint cannot run in production .venv")
    if not _same_lexical_path(running_prefix, final):
        raise DevelopmentEnvironmentError(
            f"Checkpoint interpreter resolves to {running_prefix}, expected {final}"
        )
    verify_environment_manifest(
        final,
        expected_final=final,
        config=config,
        identity=identity,
        fingerprint=fingerprint,
    )
    return final


def checkpoint_commands(
    config: EnvironmentConfig,
    environment: Path,
    paths: CheckpointPaths,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    python = str(environment / "bin/python")
    return (
        (
            "compile",
            (
                python,
                "-m",
                "compileall",
                "-q",
                "automation",
                "core",
                "handlers",
                "tools",
                "utils",
                "main.py",
            ),
        ),
        (
            "state definitions",
            (python, "test/validate_state_defs.py"),
        ),
        (
            "clickmap integrity",
            (python, "test/clickmap_integrity.py", "--show-orphans"),
        ),
        (
            "pytest",
            (
                python,
                "-m",
                "pytest",
                "-q",
                "-p",
                "tools.development_pytest",
                "-o",
                f"cache_dir={paths.pytest_cache}",
            ),
        ),
    )


def checkpoint_environment(
    base: Mapping[str, str],
    *,
    environment: Path,
    paths: CheckpointPaths,
) -> dict[str, str]:
    result = dict(base)
    for key in (
        "ADB_DEVICE",
        "ANDROID_SERIAL",
        "PYTHONHOME",
        "PYTHONPATH",
        "THETOWER_ADB_PORT",
        "THETOWER_CONTROL_TOKEN",
        "THETOWER_PLAYER_SAVE_AUDIT",
        "THETOWER_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS",
        "THETOWER_STARTUP_GATES",
        "THETOWER_STRATEGY",
    ):
        result.pop(key, None)
    result.update(
        {
            "COVERAGE_FILE": str(paths.coverage_file),
            "PATH": f"{paths.blocked_tools}:{environment / 'bin'}:/usr/bin:/bin",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPYCACHEPREFIX": str(paths.bytecode),
            "TMPDIR": str(paths.scratch),
            "TOWER_ACTION_LOG_PATH": str(paths.logs / "actions.log"),
            "THETOWER_DEVELOPMENT_LOG_DIR": str(paths.logs),
            "THETOWER_DEVELOPMENT_SCREENSHOT_DIR": str(paths.screenshots),
            "THETOWER_DEVELOPMENT_SCRATCH_DIR": str(paths.scratch),
            "THETOWER_CHECKPOINT_EXCLUDE_HOST_TOOLS": "1",
            "THETOWER_STRATEGY_PROFILE_DIR": str(paths.custom_configuration),
            "VIRTUAL_ENV": str(environment),
            "XDG_CACHE_HOME": str(paths.root / "cache"),
        }
    )
    return result


def run_checkpoint(
    config: EnvironmentConfig,
    environment: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    parent = config.repository_root / "tmp" / "development-checkpoint"
    _prepare_checkpoint_parent(config.repository_root, parent)
    state_root = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
    paths = CheckpointPaths.beneath(state_root)
    try:
        for directory in (
            paths.bytecode,
            paths.pytest_cache,
            paths.coverage_file.parent,
            paths.logs,
            paths.screenshots,
            paths.custom_configuration,
            paths.scratch,
            paths.blocked_tools,
        ):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        _create_blocked_host_tools(paths.blocked_tools)
        for name, available in host_prerequisite_inventory(os.environ).items():
            print(
                "[checkpoint] host prerequisite "
                f"{name}={'present' if available else 'missing'} (excluded)",
                flush=True,
            )
        environment_variables = checkpoint_environment(
            os.environ,
            environment=environment,
            paths=paths,
        )
        for label, command in checkpoint_commands(config, environment, paths):
            print(f"[checkpoint] {label}", flush=True)
            completed = run(
                list(command),
                cwd=config.repository_root,
                env=environment_variables,
                text=True,
            )
            if completed.returncode:
                print(
                    f"[checkpoint] FAILED {label} (exit {completed.returncode})",
                    file=sys.stderr,
                )
                return int(completed.returncode)
        print("[checkpoint] PASS")
        return 0
    finally:
        shutil.rmtree(state_root, ignore_errors=False)


def host_prerequisite_inventory(base: Mapping[str, str]) -> dict[str, bool]:
    search_path = base.get("PATH")
    return {
        name: shutil.which(name, path=search_path) is not None
        for name in CHECKPOINT_TOOL_BLOCKLIST
    }


def regenerate_locks(config: EnvironmentConfig) -> None:
    verify_interpreters(config)
    bootstrap_lock = config.repository_root / "requirements/bootstrap.lock"
    parse_lock_file(bootstrap_lock)
    with tempfile.TemporaryDirectory(prefix="thetower-lock-regeneration-") as temporary:
        temporary_root = Path(temporary)
        resolver = temporary_root / "resolver"
        cache = temporary_root / "cache"
        source_root = temporary_root / "source"
        (source_root / "requirements").mkdir(parents=True)
        for relative in (
            "pyproject.toml",
            "requirements/bootstrap.in",
            "requirements/bootstrap.lock",
            "requirements/runtime.lock",
            "requirements/development.lock",
        ):
            source = config.repository_root / relative
            destination = source_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(_read_regular_no_follow(source))
        environment = _isolated_subprocess_environment(cache)
        _run_checked(
            [str(config.interpreter), "-I", "-m", "venv", str(resolver)],
            description="lock resolver creation",
            env=environment,
        )
        python = resolver / "bin/python"
        _run_checked(
            [
                str(python),
                "-I",
                "-m",
                "pip",
                "install",
                "--require-hashes",
                "--only-binary=:all:",
                "--no-deps",
                "--force-reinstall",
                "--no-input",
                "-r",
                str(source_root / "requirements/bootstrap.lock"),
            ],
            description="lock resolver toolchain installation",
            env=environment,
        )
        compile_base = [
            str(python),
            "-I",
            "-m",
            "piptools",
            "compile",
            "--cache-dir",
            str(cache),
            "--generate-hashes",
            "--allow-unsafe",
            "--strip-extras",
            "--resolver=backtracking",
            "--no-emit-index-url",
            "--no-emit-trusted-host",
        ]
        jobs = (
            (
                "requirements/bootstrap.lock",
                "requirements/bootstrap.in",
                (),
            ),
            (
                "requirements/runtime.lock",
                "pyproject.toml",
                ("--extra", "player-save"),
            ),
            (
                "requirements/development.lock",
                "pyproject.toml",
                ("--all-extras",),
            ),
        )
        environment["CUSTOM_COMPILE_COMMAND"] = LOCK_REGENERATION_COMMAND
        for output, source, extra in jobs:
            _run_checked(
                [
                    *compile_base,
                    *extra,
                    "--output-file",
                    output,
                    source,
                ],
                description=f"{output} regeneration",
                cwd=source_root,
                env=environment,
            )
            generated = source_root / output
            _annotate_lock(generated, LOCK_SOURCES[output])
            _atomic_replace_regular_file(
                config.repository_root / output,
                _read_regular_no_follow(generated),
            )
    validate_lock_inputs(config)


def _annotate_lock(path: Path, source: str) -> None:
    text = path.read_text(encoding="utf-8")
    lines = [
        line
        for line in text.splitlines()
        if not line.startswith("# Source declaration:")
        and not line.startswith("# Regenerate:")
    ]
    rendered = (
        f"# Source declaration: {source}\n"
        f"# Regenerate: {LOCK_REGENERATION_COMMAND}\n"
        + "\n".join(lines)
        + "\n"
    )
    path.write_text(rendered, encoding="utf-8")


def _prepare_checkpoint_parent(repository: Path, parent: Path) -> None:
    if not _path_is_within(parent, repository):
        raise DevelopmentEnvironmentError(
            "Checkpoint state root must remain inside its worktree"
        )
    for directory in (repository, repository / "tmp", parent):
        try:
            details = directory.lstat()
        except FileNotFoundError:
            try:
                os.mkdir(directory, 0o700)
            except OSError as exc:
                raise DevelopmentEnvironmentError(
                    f"Unable to create checkpoint directory {directory}: {exc}"
                ) from exc
            details = directory.lstat()
        if not stat.S_ISDIR(details.st_mode) or directory.is_symlink():
            raise DevelopmentEnvironmentError(
                f"Checkpoint directory is not no-follow: {directory}"
            )
        if details.st_uid != os.getuid() or (
            directory != repository and stat.S_IMODE(details.st_mode) & 0o022
        ):
            raise DevelopmentEnvironmentError(
                f"Checkpoint directory has unsafe ownership or permissions: {directory}"
            )


def _atomic_replace_regular_file(destination: Path, payload: bytes) -> None:
    temporary = destination.with_name(
        f".{destination.name}.replace-{secrets.token_hex(8)}"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o644)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fchmod(descriptor, 0o644)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        if os.path.lexists(temporary):
            temporary.unlink()


def _ensure_environment_root(root: Path) -> None:
    parent = root.parent
    try:
        parent_details = parent.lstat()
    except OSError as exc:
        raise DevelopmentEnvironmentError(
            f"Environment-store parent {parent} is unavailable: {exc}"
        ) from exc
    if not stat.S_ISDIR(parent_details.st_mode) or parent.is_symlink():
        raise DevelopmentEnvironmentError("Environment-store parent is not no-follow")
    try:
        os.mkdir(root, 0o700)
    except FileExistsError:
        pass
    _ensure_private_directory(root, create=False, require_owner_write=True)


def _ensure_private_directory(
    path: Path,
    *,
    create: bool,
    require_owner_write: bool = False,
) -> None:
    if create:
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        except FileNotFoundError as exc:
            raise DevelopmentEnvironmentError(
                f"Private directory parent is unavailable for {path}"
            ) from exc
        except OSError as exc:
            raise DevelopmentEnvironmentError(
                f"Unable to create private directory {path}: {exc}"
            ) from exc
    try:
        details = path.lstat()
    except OSError as exc:
        raise DevelopmentEnvironmentError(f"Private directory {path} is unavailable: {exc}") from exc
    mode = stat.S_IMODE(details.st_mode)
    if not stat.S_ISDIR(details.st_mode) or path.is_symlink():
        raise DevelopmentEnvironmentError(f"Private directory {path} is not no-follow")
    if details.st_uid != os.getuid() or mode & 0o022:
        raise DevelopmentEnvironmentError(
            f"Private directory {path} has unsafe ownership or permissions"
        )
    if require_owner_write and not mode & 0o200:
        raise DevelopmentEnvironmentError(f"Private directory {path} is not owner-writable")


def _create_stage_directory(root: Path, final: Path) -> Path:
    descriptor = _open_directory_no_follow(root)
    try:
        for _ in range(32):
            name = f".{final.name}.stage-{secrets.token_hex(8)}"
            try:
                os.mkdir(name, 0o700, dir_fd=descriptor)
            except FileExistsError:
                continue
            stage = root / name
            validate_stage_path(final, stage)
            return stage
    finally:
        os.close(descriptor)
    raise DevelopmentEnvironmentError("Unable to allocate a unique environment stage")


def cleanup_owned_stage(stage: Path, final: Path) -> None:
    validate_stage_path(final, stage)
    for relative, details in _iter_tree(stage):
        if stat.S_ISDIR(details.st_mode):
            os.chmod(stage / relative, 0o700)
    os.chmod(stage, 0o700)
    shutil.rmtree(stage)
    _fsync_directory(stage.parent)


def _publish_stage(root: Path, stage: Path, final: Path) -> None:
    validate_stage_path(final, stage)
    descriptor = _open_directory_no_follow(root)
    try:
        try:
            os.stat(final.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise DevelopmentEnvironmentError(
                f"Final environment appeared during build: {final}"
            )
        os.rename(
            stage.name,
            final.name,
            src_dir_fd=descriptor,
            dst_dir_fd=descriptor,
        )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _make_contents_immutable(root: Path) -> None:
    entries = list(_iter_tree(root))
    for relative, details in entries:
        path = root / relative
        if stat.S_ISREG(details.st_mode):
            executable = bool(stat.S_IMODE(details.st_mode) & 0o111)
            os.chmod(path, 0o555 if executable else 0o444)
    for relative, details in sorted(
        entries, key=lambda item: len(item[0].parts), reverse=True
    ):
        if stat.S_ISDIR(details.st_mode):
            os.chmod(root / relative, 0o555)


def _sync_tree(root: Path) -> None:
    directories = [root]
    for relative, details in _iter_tree(root):
        path = root / relative
        if stat.S_ISREG(details.st_mode):
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        elif stat.S_ISDIR(details.st_mode):
            directories.append(path)
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _fsync_directory(directory)


def _iter_tree(root: Path) -> Iterator[tuple[Path, os.stat_result]]:
    stack: list[tuple[Path, Path]] = [(root, Path())]
    while stack:
        directory, relative_directory = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name, reverse=True)
        except OSError as exc:
            raise DevelopmentEnvironmentError(
                f"Unable to scan environment directory {directory}: {exc}"
            ) from exc
        for entry in entries:
            relative = relative_directory / entry.name
            details = entry.stat(follow_symlinks=False)
            yield relative, details
            if stat.S_ISDIR(details.st_mode):
                stack.append((Path(entry.path), relative))


def _validate_environment_symlink(root: Path, item: Mapping[str, object]) -> None:
    relative = Path(str(item["path"]))
    target = str(item["target"])
    if os.path.isabs(target):
        configured = str((root / "bin/python").resolve(strict=False))
        if target != configured and target != "/usr/bin/python3.12":
            raise DevelopmentEnvironmentError(
                f"Environment symlink escapes to unsupported target: {relative}"
            )
        return
    destination = Path(os.path.abspath(root / relative.parent / target))
    if not _path_is_within(destination, root):
        raise DevelopmentEnvironmentError(
            f"Environment symlink escapes its root: {relative}"
        )


def _create_blocked_host_tools(directory: Path) -> None:
    for name in CHECKPOINT_TOOL_BLOCKLIST:
        path = directory / name
        path.write_text(
            "#!/bin/sh\n"
            f"echo 'development checkpoint forbids host tool: {name}' >&2\n"
            "exit 97\n",
            encoding="utf-8",
        )
        path.chmod(0o500)


def _isolated_subprocess_environment(cache: Path) -> dict[str, str]:
    result = dict(os.environ)
    for key in list(result):
        if key.startswith("PIP_") or key in {"PYTHONHOME", "PYTHONPATH"}:
            result.pop(key, None)
    result.update(
        {
            "PIP_CACHE_DIR": str(cache),
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return result


def _run_checked(
    command: Sequence[str],
    *,
    description: str,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        output = "\n".join(
            (completed.stderr or completed.stdout or "no diagnostic output")
            .strip()
            .splitlines()[-30:]
        )
        raise DevelopmentEnvironmentError(
            f"{description} failed with exit {completed.returncode}:\n{output}"
        )
    return completed


def _read_regular_no_follow(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise DevelopmentEnvironmentError(f"Expected regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _write_json_no_follow(path: Path, payload: object) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        rendered = (
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        view = memoryview(rendered)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _file_contains(path: Path, needle: bytes) -> bool:
    if not needle:
        return False
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        overlap = b""
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return False
            combined = overlap + chunk
            if needle in combined:
                return True
            overlap = combined[-(len(needle) - 1) :] if len(needle) > 1 else b""
    finally:
        os.close(descriptor)


def _open_directory_no_follow(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    details = os.fstat(descriptor)
    if not stat.S_ISDIR(details.st_mode):
        os.close(descriptor)
        raise DevelopmentEnvironmentError(f"Expected directory: {path}")
    return descriptor


def _fsync_directory(path: Path) -> None:
    descriptor = _open_directory_no_follow(path)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _same_lexical_path(left: Path, right: Path) -> bool:
    return os.path.normpath(os.path.abspath(left)) == os.path.normpath(
        os.path.abspath(right)
    )


def _path_is_within(path: Path, parent: Path) -> bool:
    path_text = os.path.normpath(os.path.abspath(path))
    parent_text = os.path.normpath(os.path.abspath(parent))
    try:
        return os.path.commonpath((path_text, parent_text)) == parent_text
    except ValueError:
        return False


def _require_no_symlink_ancestors(path: Path, root: Path) -> None:
    if not _path_is_within(path, root):
        raise DevelopmentEnvironmentError(f"Path escapes no-follow root: {path}")
    current = path.parent
    while not _same_lexical_path(current, root):
        try:
            details = current.lstat()
        except OSError as exc:
            raise DevelopmentEnvironmentError(
                f"No-follow ancestor is unavailable for {path}: {exc}"
            ) from exc
        if not stat.S_ISDIR(details.st_mode) or current.is_symlink():
            raise DevelopmentEnvironmentError(
                f"Path has a symlink or non-directory ancestor: {path}"
            )
        parent = current.parent
        if _same_lexical_path(parent, current):
            raise DevelopmentEnvironmentError(f"Unable to bound no-follow path: {path}")
        current = parent


def _print_status(
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: EnvironmentFingerprint,
) -> int:
    final = expected_environment_path(config, identity, fingerprint.digest)
    if not os.path.lexists(final):
        print(f"status=missing\nfingerprint={fingerprint.digest}\nenvironment={final}")
        return 1
    verify_published_environment(final, config, identity, fingerprint)
    verify_worktree_environment_link(config, final)
    print(f"status=ready\nfingerprint={fingerprint.digest}\nenvironment={final}")
    return 0


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("bootstrap", help="build or reuse the locked development environment")
    subparsers.add_parser("status", help="verify the expected environment and worktree link")
    subparsers.add_parser("checkpoint", help="run the complete ordinary non-live checkpoint")
    subparsers.add_parser("verify-locks", help="validate exact pins and artifact hashes")
    subparsers.add_parser("lock", help="regenerate all hash-checked locks deterministically")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_argument_parser().parse_args(argv)
    try:
        config = load_config(repository_root())
        identity = verify_interpreters(config)
        if arguments.command == "lock":
            regenerate_locks(config)
            print("locks=regenerated")
            return 0
        locks = validate_lock_inputs(config)
        fingerprint = compute_environment_fingerprint(config, identity)
        if arguments.command == "verify-locks":
            counts = ",".join(
                f"{Path(path).name}:{len(packages)}"
                for path, packages in sorted(locks.items())
            )
            print(f"locks=valid\npackages={counts}\nfingerprint={fingerprint.digest}")
            return 0
        if arguments.command == "status":
            return _print_status(config, identity, fingerprint)
        if arguments.command == "bootstrap":
            result = provision_environment(config, identity, fingerprint)
            print(
                f"action={'reused' if result.reused else 'created'}\n"
                f"fingerprint={result.fingerprint}\n"
                f"environment={result.environment}\n"
                f"worktree_link={config.repository_root / '.venv'}"
            )
            return 0
        if arguments.command == "checkpoint":
            environment = require_active_development_environment(
                config, identity, fingerprint
            )
            return run_checkpoint(config, environment)
        raise DevelopmentEnvironmentError(f"Unsupported command {arguments.command!r}")
    except DevelopmentEnvironmentError as exc:
        print(f"development: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
