#!/usr/bin/python3.12
"""Reproduce and validate an isolated TheTower development environment.

The entrypoint is deliberately standard-library-only so the configured host
interpreter can run ``bootstrap``, ``status``, ``verify-locks``, and ``lock``
before a worktree ``.venv`` exists. ``checkpoint`` additionally requires the
completed environment selected by the current worktree.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
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
from typing import Callable, Iterator, Mapping, Sequence


CONFIG_RELATIVE_PATH = Path("requirements/development-environment.json")
COMPLETION_MARKER_NAME = "THE_TOWER_ENVIRONMENT_COMPLETE.json"
COMPLETION_MARKER_SCHEMA = 1
WRITER_LOCK_NAME = "development-environment.write.lock"
LOCK_REGENERATION_COMMAND = ".venv/bin/python tools/development.py lock"
LOCK_SOURCES = {
    "requirements/bootstrap.lock": "requirements/bootstrap.in",
    "requirements/runtime.lock": "pyproject.toml (runtime + player-save)",
    "requirements/development.lock": "pyproject.toml (all optional groups)",
}
_LOCKED_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s;\\]+)(?:\s|$)"
)
_SHA256_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})(?:\s|$)")


class DevelopmentEnvironmentError(RuntimeError):
    """A development bootstrap, lock, or checkpoint error."""


class IncompleteEnvironmentError(DevelopmentEnvironmentError):
    """A fingerprinted environment has not published its completion marker."""


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
        )


BuildEnvironment = Callable[
    [EnvironmentConfig, InterpreterIdentity, EnvironmentFingerprint, Path], None
]
VerifyEnvironmentContents = Callable[
    [Path, EnvironmentConfig, InterpreterIdentity, EnvironmentFingerprint], None
]


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
        raw = json.loads(config_path.read_text(encoding="utf-8"))
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
        version = (root / ".python-version").read_text(encoding="utf-8").strip()
    except (KeyError, TypeError, ValueError, OSError, UnicodeError) as exc:
        raise DevelopmentEnvironmentError(
            f"{CONFIG_RELATIVE_PATH} is incomplete: {exc}"
        ) from exc
    if schema != 3:
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
    if _same_path(root, config.production_environment.parent):
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
    if _same_path(config.environment_root, config.production_environment) or (
        _path_is_within(config.environment_root, config.production_environment)
        or _path_is_within(config.production_environment, config.environment_root)
    ):
        raise DevelopmentEnvironmentError(
            "Development and production environment roots must be separate"
        )
    if _path_is_within(config.interpreter, config.production_environment):
        raise DevelopmentEnvironmentError(
            "The configured development interpreter cannot come from production .venv"
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
    if not interpreter.is_file():
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
        try:
            payload = (config.repository_root / candidate).read_bytes()
        except OSError as exc:
            raise DevelopmentEnvironmentError(
                f"Unable to read dependency input {relative}: {exc}"
            ) from exc
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
    if not _same_path(final, expected):
        raise DevelopmentEnvironmentError(
            f"Environment path {final} does not match expected {expected}"
        )
    if not _same_path(final.parent, config.environment_root):
        raise DevelopmentEnvironmentError(
            "Development environment is not a direct child of its configured store"
        )
    if _same_path(final, config.production_environment) or _path_is_within(
        final, config.production_environment
    ):
        raise DevelopmentEnvironmentError("Production .venv is never a development path")


def _validate_existing_environment(
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: EnvironmentFingerprint,
    final: Path,
) -> None:
    validate_final_path(config, identity, fingerprint.digest, final)
    try:
        details = final.lstat()
    except OSError as exc:
        raise DevelopmentEnvironmentError(
            f"Development environment {final} is unavailable: {exc}"
        ) from exc
    if not stat.S_ISDIR(details.st_mode) or final.is_symlink():
        raise DevelopmentEnvironmentError(
            f"Development environment {final} must be a real directory"
        )


def parse_lock_file(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
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
        match = _LOCKED_REQUIREMENT.match(line)
        if match is None:
            raise DevelopmentEnvironmentError(
                f"Lock {path} contains a non-exact requirement: {line!r}"
            )
        name = canonical_distribution_name(match.group(1))
        version = match.group(2)
        if not _SHA256_HASH.findall(line):
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
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise DevelopmentEnvironmentError(
                f"Unable to read lock {relative}: {exc}"
            ) from exc
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
            (config.repository_root / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        base = _direct_requirements(project["dependencies"])
        extras = project["optional-dependencies"]
        player_save = _direct_requirements(extras["player-save"])
        developer_tools = _direct_requirements(extras["developer-tools"])
        tests = _direct_requirements(extras["test"])
        bootstrap_direct = _requirements_from_input(
            config.repository_root / "requirements/bootstrap.in"
        )
    except (KeyError, TypeError, OSError, tomllib.TOMLDecodeError, UnicodeError) as exc:
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
        name = canonical_distribution_name(match.group(1))
        if name in result:
            raise DevelopmentEnvironmentError(f"Direct dependency repeats {name}")
        result[name] = match.group(2)
    return result


def _requirements_from_input(path: Path) -> dict[str, str]:
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
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
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        handle = (root / WRITER_LOCK_NAME).open("a+b")
    except OSError as exc:
        raise DevelopmentEnvironmentError(
            f"Unable to open development writer lock {root / WRITER_LOCK_NAME}: {exc}"
        ) from exc
    with handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def provision_environment(
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: EnvironmentFingerprint,
    *,
    build: BuildEnvironment | None = None,
    verify_contents: VerifyEnvironmentContents | None = None,
) -> ProvisionResult:
    builder = build or build_environment
    verifier = verify_contents or verify_environment_contents
    final = expected_environment_path(config, identity, fingerprint.digest)
    validate_final_path(config, identity, fingerprint.digest, final)
    with development_writer_lock(config):
        _ensure_environment_root(config)
        if os.path.lexists(final):
            _validate_existing_environment(config, identity, fingerprint, final)
            marker = final / COMPLETION_MARKER_NAME
            if not os.path.lexists(marker):
                remove_incomplete_environment(config, identity, fingerprint, final)
            else:
                try:
                    verify_completion_marker(
                        final, config=config, identity=identity, fingerprint=fingerprint
                    )
                    verifier(final, config, identity, fingerprint)
                except Exception as exc:
                    raise DevelopmentEnvironmentError(
                        f"Completed development environment {final} is invalid; "
                        f"refusing to modify it automatically: {exc}"
                    ) from exc
                replace_worktree_environment_link(config, final)
                return ProvisionResult(final, fingerprint.digest, True)

        try:
            final.mkdir(mode=0o700)
        except OSError as exc:
            raise DevelopmentEnvironmentError(
                f"Unable to create development environment {final}: {exc}"
            ) from exc
        builder(config, identity, fingerprint, final)
        verifier(final, config, identity, fingerprint)
        write_completion_marker(
            final, config=config, identity=identity, fingerprint=fingerprint
        )
        replace_worktree_environment_link(config, final)
        return ProvisionResult(final, fingerprint.digest, False)


def remove_incomplete_environment(
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: EnvironmentFingerprint,
    final: Path,
) -> None:
    """Remove only the exact incomplete child selected by current inputs."""

    _validate_existing_environment(config, identity, fingerprint, final)
    marker = final / COMPLETION_MARKER_NAME
    if os.path.lexists(marker):
        raise DevelopmentEnvironmentError(
            f"Refusing to remove {final}: a completion marker is present"
        )
    try:
        shutil.rmtree(final)
    except OSError as exc:
        raise DevelopmentEnvironmentError(
            f"Unable to remove incomplete development environment {final}: {exc}"
        ) from exc


def build_environment(
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: EnvironmentFingerprint,
    final: Path,
) -> None:
    require_supported_identity(config, identity)
    _validate_existing_environment(config, identity, fingerprint, final)
    cache = final / ".artifact-cache"
    environment = _isolated_subprocess_environment(cache)
    _run_checked(
        [str(config.interpreter), "-I", "-m", "venv", str(final)],
        description="development virtual-environment creation",
        env=environment,
    )
    python = final / "bin/python"
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
            str(python),
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
        [str(python), "-I", "-m", "pip", "check"],
        description="installed dependency consistency check",
        env=environment,
    )
    if cache.exists():
        shutil.rmtree(cache)


def _completion_marker_payload(
    config: EnvironmentConfig,
    fingerprint: EnvironmentFingerprint,
) -> dict[str, object]:
    return {
        "bootstrap_schema": config.bootstrap_schema,
        "environment_fingerprint": fingerprint.digest,
        "schema_version": COMPLETION_MARKER_SCHEMA,
    }


def write_completion_marker(
    final: Path,
    *,
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: EnvironmentFingerprint,
) -> None:
    _validate_existing_environment(config, identity, fingerprint, final)
    marker = final / COMPLETION_MARKER_NAME
    if os.path.lexists(marker):
        raise DevelopmentEnvironmentError(
            f"Completion marker already exists in new environment {final}"
        )
    temporary = final / f".{COMPLETION_MARKER_NAME}.tmp-{secrets.token_hex(8)}"
    try:
        temporary.write_text(
            json.dumps(
                _completion_marker_payload(config, fingerprint),
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, marker)
    finally:
        if os.path.lexists(temporary):
            temporary.unlink()


def verify_completion_marker(
    final: Path,
    *,
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: EnvironmentFingerprint,
) -> None:
    _validate_existing_environment(config, identity, fingerprint, final)
    marker = final / COMPLETION_MARKER_NAME
    if not os.path.lexists(marker):
        raise IncompleteEnvironmentError(
            f"Development environment {final} is incomplete: completion marker is absent"
        )
    try:
        details = marker.lstat()
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DevelopmentEnvironmentError(
            f"Environment completion marker is unreadable: {exc}"
        ) from exc
    if not stat.S_ISREG(details.st_mode) or marker.is_symlink():
        raise DevelopmentEnvironmentError(
            "Environment completion marker must be a regular file"
        )
    if payload != _completion_marker_payload(config, fingerprint):
        raise DevelopmentEnvironmentError(
            "Environment completion marker does not match the expected fingerprint"
        )


def verify_environment_contents(
    final: Path,
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: EnvironmentFingerprint,
) -> None:
    _validate_existing_environment(config, identity, fingerprint, final)
    expected = _expected_installed_distributions(config)
    _verify_environment_python(
        final,
        identity=identity,
        expected_distributions=expected,
    )
    with tempfile.TemporaryDirectory(prefix="thetower-environment-check-") as cache:
        _run_checked(
            [str(final / "bin/python"), "-I", "-m", "pip", "check"],
            description="completed environment dependency check",
            env=_isolated_subprocess_environment(Path(cache)),
        )


def verify_completed_environment(
    final: Path,
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: EnvironmentFingerprint,
) -> None:
    verify_completion_marker(
        final, config=config, identity=identity, fingerprint=fingerprint
    )
    verify_environment_contents(final, config, identity, fingerprint)


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
    identity: InterpreterIdentity,
    expected_distributions: Mapping[str, str],
) -> None:
    program = (
        "import importlib.metadata,json,os,platform,re,sys,sysconfig;"
        "installed={};"
        "[(installed.setdefault(re.sub(r'[-_.]+','-',d.metadata['Name']).lower(),d.version)) "
        "for d in importlib.metadata.distributions() if d.metadata.get('Name')];"
        "print(json.dumps({'prefix':os.path.realpath(sys.prefix),"
        "'implementation':sys.implementation.name,'version':platform.python_version(),"
        "'system':platform.system(),'machine':platform.machine(),"
        "'platform_tag':sysconfig.get_platform(),'installed':installed},sort_keys=True))"
    )
    with tempfile.TemporaryDirectory(prefix="thetower-environment-check-") as cache:
        environment = _isolated_subprocess_environment(Path(cache))
        completed = _run_checked(
            [str(root / "bin/python"), "-I", "-B", "-c", program],
            description="completed environment interpreter check",
            env=environment,
        )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise DevelopmentEnvironmentError(
                "Completed environment returned invalid identity data"
            ) from exc
        observed = InterpreterIdentity(
            implementation=str(payload.get("implementation")),
            version=str(payload.get("version")),
            system=str(payload.get("system")),
            machine=str(payload.get("machine")),
            platform_tag=str(payload.get("platform_tag")),
        )
        if observed != identity:
            raise DevelopmentEnvironmentError("Completed environment identity mismatch")
        if payload.get("prefix") != os.path.realpath(root):
            raise DevelopmentEnvironmentError(
                "Completed environment interpreter resolves to the wrong prefix"
            )
        if payload.get("installed") != dict(sorted(expected_distributions.items())):
            raise DevelopmentEnvironmentError(
                "Completed environment distribution set does not match the locks"
            )
        _run_checked(
            [str(root / "bin/python"), "-I", "-B", "-m", "pytest", "--version"],
            description="completed pytest module check",
            env=environment,
        )


def replace_worktree_environment_link(config: EnvironmentConfig, final: Path) -> None:
    link = config.repository_root / ".venv"
    if os.path.lexists(link) and not link.is_symlink():
        raise DevelopmentEnvironmentError(
            f"Refusing to replace non-symlink worktree environment {link}"
        )
    temporary = config.repository_root / f".venv.link-{secrets.token_hex(8)}"
    try:
        os.symlink(str(final), temporary, target_is_directory=True)
        os.replace(temporary, link)
    except OSError as exc:
        raise DevelopmentEnvironmentError(
            f"Unable to select development environment through {link}: {exc}"
        ) from exc
    finally:
        if os.path.lexists(temporary):
            temporary.unlink()
    verify_worktree_environment_link(config, final)


def verify_worktree_environment_link(config: EnvironmentConfig, final: Path) -> None:
    link = config.repository_root / ".venv"
    try:
        details = link.lstat()
    except OSError as exc:
        raise DevelopmentEnvironmentError(
            f"Worktree .venv link is unavailable: {exc}"
        ) from exc
    if not stat.S_ISLNK(details.st_mode):
        raise DevelopmentEnvironmentError("Worktree .venv must be a symlink")
    target = os.readlink(link)
    target_path = Path(target)
    if target_path.is_absolute() and (
        _same_path(target_path, config.production_environment)
        or _path_is_within(target_path, config.production_environment)
    ):
        raise DevelopmentEnvironmentError("Worktree .venv points at production")
    if not target_path.is_absolute() or not _same_path(target_path, final):
        raise DevelopmentEnvironmentError(
            f"Worktree .venv targets {target!r}, expected {final}"
        )


def require_active_development_environment(
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: EnvironmentFingerprint,
) -> Path:
    final = expected_environment_path(config, identity, fingerprint.digest)
    verify_worktree_environment_link(config, final)
    running_prefix = Path(os.path.realpath(sys.prefix))
    if _same_path(running_prefix, config.production_environment):
        raise DevelopmentEnvironmentError("Checkpoint cannot run in production .venv")
    if not _same_path(running_prefix, final):
        raise DevelopmentEnvironmentError(
            f"Checkpoint interpreter resolves to {running_prefix}, expected {final}"
        )
    try:
        verify_completed_environment(final, config, identity, fingerprint)
    except DevelopmentEnvironmentError as exc:
        raise DevelopmentEnvironmentError(
            f"Selected development environment is invalid: {exc}"
        ) from exc
    return final


def checkpoint_commands(
    config: EnvironmentConfig,
    environment: Path,
    paths: CheckpointPaths,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    del config
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
        ("state definitions", (python, "test/validate_state_defs.py")),
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
        "COVERAGE_PROCESS_START",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTEST_ADDOPTS",
        "THETOWER_ADB_CONNECTION_OWNER",
        "THETOWER_ADB_PORT",
        "THETOWER_CONTROL_TOKEN",
        "THETOWER_PLAYER_SAVE_AUDIT",
        "THETOWER_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS",
        "THETOWER_STARTUP_GATES",
        "THETOWER_STRATEGY",
        "VIRTUAL_ENV",
    ):
        result.pop(key, None)
    result.update(
        {
            "COVERAGE_FILE": str(paths.coverage_file),
            "PATH": f"{environment / 'bin'}:/usr/local/bin:/usr/bin:/bin",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPYCACHEPREFIX": str(paths.bytecode),
            "TMPDIR": str(paths.scratch),
            "TOWER_ACTION_LOG_PATH": str(paths.logs / "actions.log"),
            "THETOWER_DEVELOPMENT_LOG_DIR": str(paths.logs),
            "THETOWER_DEVELOPMENT_SCREENSHOT_DIR": str(paths.screenshots),
            "THETOWER_DEVELOPMENT_SCRATCH_DIR": str(paths.scratch),
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
        ):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
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
        shutil.rmtree(state_root)


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
            destination.write_bytes(source.read_bytes())
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
            ("requirements/bootstrap.lock", "requirements/bootstrap.in", ()),
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
                generated.read_bytes(),
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
    path.write_text(
        f"# Source declaration: {source}\n"
        f"# Regenerate: {LOCK_REGENERATION_COMMAND}\n"
        + "\n".join(lines)
        + "\n",
        encoding="utf-8",
    )


def _atomic_replace_regular_file(destination: Path, payload: bytes) -> None:
    temporary = destination.with_name(
        f".{destination.name}.replace-{secrets.token_hex(8)}"
    )
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, destination)
    finally:
        if os.path.lexists(temporary):
            temporary.unlink()


def _ensure_environment_root(config: EnvironmentConfig) -> None:
    root = config.environment_root
    parent = root.parent
    try:
        parent_details = parent.lstat()
    except OSError as exc:
        raise DevelopmentEnvironmentError(
            f"Environment-store parent {parent} is unavailable: {exc}"
        ) from exc
    if not stat.S_ISDIR(parent_details.st_mode) or parent.is_symlink():
        raise DevelopmentEnvironmentError(
            f"Environment-store parent {parent} must be a real directory"
        )
    try:
        root.mkdir(mode=0o700, exist_ok=True)
        root_details = root.lstat()
    except OSError as exc:
        raise DevelopmentEnvironmentError(
            f"Unable to prepare environment store {root}: {exc}"
        ) from exc
    if not stat.S_ISDIR(root_details.st_mode) or root.is_symlink():
        raise DevelopmentEnvironmentError(
            f"Environment store {root} must be a real directory"
        )


def _prepare_checkpoint_parent(repository: Path, parent: Path) -> None:
    if not _path_is_within(parent, repository):
        raise DevelopmentEnvironmentError(
            "Checkpoint state root must remain inside its worktree"
        )
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise DevelopmentEnvironmentError(
            f"Unable to create checkpoint directory {parent}: {exc}"
        ) from exc
    for directory in (repository, repository / "tmp", parent):
        try:
            details = directory.lstat()
        except OSError as exc:
            raise DevelopmentEnvironmentError(
                f"Checkpoint directory {directory} is unavailable: {exc}"
            ) from exc
        if not stat.S_ISDIR(details.st_mode) or directory.is_symlink():
            raise DevelopmentEnvironmentError(
                f"Checkpoint directory must be a real directory: {directory}"
            )


def _isolated_subprocess_environment(cache: Path) -> dict[str, str]:
    result = dict(os.environ)
    for key in list(result):
        if key.startswith("PIP_") or key in {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}:
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


def _same_path(left: Path, right: Path) -> bool:
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


def _print_status(
    config: EnvironmentConfig,
    identity: InterpreterIdentity,
    fingerprint: EnvironmentFingerprint,
) -> int:
    final = expected_environment_path(config, identity, fingerprint.digest)
    if not os.path.lexists(final):
        print(f"status=missing\nfingerprint={fingerprint.digest}\nenvironment={final}")
        return 1
    if not os.path.lexists(final / COMPLETION_MARKER_NAME):
        raise IncompleteEnvironmentError(
            f"Development environment {final} is incomplete: completion marker is absent"
        )
    try:
        verify_completed_environment(final, config, identity, fingerprint)
    except DevelopmentEnvironmentError as exc:
        raise DevelopmentEnvironmentError(
            f"Completed development environment {final} is invalid: {exc}"
        ) from exc
    verify_worktree_environment_link(config, final)
    print(f"status=ready\nfingerprint={fingerprint.digest}\nenvironment={final}")
    return 0


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "bootstrap", help="build or reuse the locked development environment"
    )
    subparsers.add_parser(
        "status", help="verify the expected environment and worktree link"
    )
    subparsers.add_parser(
        "checkpoint", help="run the complete ordinary non-live checkpoint"
    )
    subparsers.add_parser(
        "verify-locks", help="validate exact pins and artifact hashes"
    )
    subparsers.add_parser(
        "lock", help="regenerate all hash-checked locks deterministically"
    )
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
