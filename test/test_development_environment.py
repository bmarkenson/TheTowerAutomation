from __future__ import annotations

from dataclasses import replace
import json
import multiprocessing
import os
from pathlib import Path
import stat
import subprocess

import pytest

from tools import development


IDENTITY = development.InterpreterIdentity(
    implementation="cpython",
    version="3.12.3",
    system="Linux",
    machine="x86_64",
    platform_tag="linux-x86_64",
)


def _config(tmp_path: Path) -> development.EnvironmentConfig:
    worktrees = tmp_path / "TheTower-worktrees"
    repository = worktrees / "workers" / "feature"
    requirements = repository / "requirements"
    requirements.mkdir(parents=True)
    config_path = repository / development.CONFIG_RELATIVE_PATH
    inputs = (
        ".python-version",
        "pyproject.toml",
        "requirements/bootstrap.in",
        "requirements/bootstrap.lock",
        "requirements/runtime.lock",
        "requirements/development.lock",
    )
    config_path.write_text('{"bootstrap_schema":2}\n', encoding="utf-8")
    for relative in inputs:
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"input={relative}\n", encoding="utf-8")
    runtime_parent = tmp_path / "runtime"
    runtime_parent.mkdir()
    return development.EnvironmentConfig(
        repository_root=repository,
        config_path=config_path,
        bootstrap_schema=2,
        dependency_inputs=inputs,
        environment_root=worktrees / ".environments",
        production_environment=tmp_path / "production" / ".venv",
        interpreter=Path("/usr/bin/python3.12"),
        supported_identity=IDENTITY,
        runtime_root=runtime_parent / "thetower",
    )


def _fingerprint(
    config: development.EnvironmentConfig,
) -> development.EnvironmentFingerprint:
    return development.compute_environment_fingerprint(config, IDENTITY)


def _create_manifest_environment(
    config: development.EnvironmentConfig,
    fingerprint: development.EnvironmentFingerprint,
    root: Path,
) -> Path:
    root.mkdir(parents=True)
    (root / "payload.txt").write_text("locked payload\n", encoding="utf-8")
    development._make_contents_immutable(root)
    manifest = development._environment_manifest(
        root,
        final=root,
        config=config,
        identity=IDENTITY,
        fingerprint=fingerprint,
    )
    development._write_json_no_follow(root / development.MANIFEST_NAME, manifest)
    os.chmod(root / development.MANIFEST_NAME, 0o444)
    os.chmod(root, 0o555)
    return root


def _restore_writable(root: Path) -> None:
    if not root.exists() or root.is_symlink():
        return
    os.chmod(root, 0o700)
    for relative, details in development._iter_tree(root):
        path = root / relative
        if stat.S_ISDIR(details.st_mode):
            os.chmod(path, 0o700)
        elif stat.S_ISREG(details.st_mode):
            os.chmod(path, 0o600)


def _lock_worker(
    config: development.EnvironmentConfig,
    entered: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
) -> None:
    with development.development_writer_lock(config):
        entered.set()
        release.wait(5)


def test_dependency_environment_fingerprint_is_deterministic(tmp_path: Path) -> None:
    config = _config(tmp_path)

    first = _fingerprint(config)
    second = _fingerprint(config)

    assert first == second
    assert list(first.input_hashes) == sorted(first.input_hashes)
    changed_input = config.repository_root / "requirements/development.lock"
    changed_input.write_text("different locked bytes\n", encoding="utf-8")
    assert _fingerprint(config).digest != first.digest


@pytest.mark.parametrize(
    "changed",
    (
        {"implementation": "pypy"},
        {"version": "3.12.4"},
        {"system": "Darwin"},
        {"machine": "aarch64"},
        {"platform_tag": "manylinux-aarch64"},
    ),
)
def test_interpreter_and_platform_mismatches_are_rejected(
    tmp_path: Path,
    changed: dict[str, str],
) -> None:
    config = _config(tmp_path)
    observed = replace(IDENTITY, **changed)

    with pytest.raises(development.DevelopmentEnvironmentError, match="Unsupported"):
        development.require_supported_identity(config, observed)


def test_production_venv_target_is_rejected(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.production_environment.parent.mkdir(parents=True)
    (config.repository_root / ".venv").symlink_to(
        config.production_environment,
        target_is_directory=True,
    )

    with pytest.raises(
        development.DevelopmentEnvironmentError,
        match="points at production",
    ):
        development.verify_worktree_environment_link(
            config,
            config.production_environment,
        )


def test_stage_and_final_paths_are_exact_and_no_follow(tmp_path: Path) -> None:
    config = _config(tmp_path)
    fingerprint = _fingerprint(config)
    config.environment_root.mkdir(mode=0o700)
    final = development.expected_environment_path(
        config,
        IDENTITY,
        fingerprint.digest,
    )
    development.validate_final_path(config, IDENTITY, fingerprint.digest, final)
    stage = config.environment_root / f".{final.name}.stage-owned"
    stage.mkdir()
    development.validate_stage_path(final, stage)

    outside = tmp_path / "outside"
    outside.mkdir()
    stage.rmdir()
    stage.symlink_to(outside, target_is_directory=True)
    with pytest.raises(development.DevelopmentEnvironmentError, match="no-follow"):
        development.validate_stage_path(final, stage)
    wrong_sibling = tmp_path / stage.name
    wrong_sibling.mkdir()
    with pytest.raises(development.DevelopmentEnvironmentError, match="sibling"):
        development.validate_stage_path(final, wrong_sibling)


def test_concurrent_builders_are_serialized_by_host_global_lock(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    context = multiprocessing.get_context("spawn")
    first_entered = context.Event()
    first_release = context.Event()
    second_entered = context.Event()
    second_release = context.Event()
    second_release.set()
    first = context.Process(
        target=_lock_worker,
        args=(config, first_entered, first_release),
    )
    second = context.Process(
        target=_lock_worker,
        args=(config, second_entered, second_release),
    )
    first.start()
    try:
        assert first_entered.wait(3)
        second.start()
        assert not second_entered.wait(0.25)
        first_release.set()
        assert second_entered.wait(3)
    finally:
        first_release.set()
        first.join(5)
        if second.pid is not None:
            second.join(5)
        if first.is_alive():
            first.terminate()
        if second.pid is not None and second.is_alive():
            second.terminate()
    assert first.exitcode == 0
    assert second.exitcode == 0


def test_failed_stage_is_cleaned_without_touching_other_paths(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    fingerprint = _fingerprint(config)
    untouched = config.environment_root.parent / "untouched"
    untouched.write_text("owner data\n", encoding="utf-8")

    def fail_build(
        _config: development.EnvironmentConfig,
        _identity: development.InterpreterIdentity,
        _fingerprint: development.EnvironmentFingerprint,
        stage: Path,
        _final: Path,
    ) -> None:
        (stage / "partial").write_text("partial\n", encoding="utf-8")
        raise RuntimeError("injected build failure")

    with pytest.raises(RuntimeError, match="injected build failure"):
        development.provision_environment(
            config,
            IDENTITY,
            fingerprint,
            build_stage=fail_build,
        )

    assert list(config.environment_root.iterdir()) == []
    assert untouched.read_text(encoding="utf-8") == "owner data\n"
    assert not os.path.lexists(config.repository_root / ".venv")


def test_immutable_valid_environment_is_reused_without_building(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    fingerprint = _fingerprint(config)
    config.environment_root.mkdir(mode=0o700)
    final = development.expected_environment_path(
        config,
        IDENTITY,
        fingerprint.digest,
    )
    final.mkdir(mode=0o555)
    before = final.stat()
    verified: list[Path] = []

    def no_build(*_args: object) -> None:
        raise AssertionError("valid immutable environment must be reused")

    def verify(
        path: Path,
        _config: development.EnvironmentConfig,
        _identity: development.InterpreterIdentity,
        _fingerprint: development.EnvironmentFingerprint,
    ) -> None:
        assert stat.S_IMODE(path.stat().st_mode) == 0o555
        verified.append(path)

    result = development.provision_environment(
        config,
        IDENTITY,
        fingerprint,
        build_stage=no_build,
        verify_final=verify,
    )

    after = final.stat()
    assert result.reused is True
    assert verified == [final]
    assert (before.st_mode, before.st_mtime_ns) == (after.st_mode, after.st_mtime_ns)
    assert os.readlink(config.repository_root / ".venv") == str(final)


def test_writable_published_environment_is_rejected(tmp_path: Path) -> None:
    config = _config(tmp_path)
    fingerprint = _fingerprint(config)
    root = _create_manifest_environment(
        config,
        fingerprint,
        tmp_path / "published-writable",
    )
    try:
        os.chmod(root, 0o755)
        with pytest.raises(
            development.DevelopmentEnvironmentError,
            match="root is writable",
        ):
            development.verify_environment_manifest(
                root,
                expected_final=root,
                config=config,
                identity=IDENTITY,
                fingerprint=fingerprint,
            )
    finally:
        _restore_writable(root)


def test_manifest_content_mismatch_is_rejected(tmp_path: Path) -> None:
    config = _config(tmp_path)
    fingerprint = _fingerprint(config)
    root = _create_manifest_environment(
        config,
        fingerprint,
        tmp_path / "published-mismatch",
    )
    try:
        payload = root / "payload.txt"
        os.chmod(payload, 0o644)
        payload.write_text("tampered payload\n", encoding="utf-8")
        os.chmod(payload, 0o444)
        with pytest.raises(
            development.DevelopmentEnvironmentError,
            match="manifest mismatch",
        ):
            development.verify_environment_manifest(
                root,
                expected_final=root,
                config=config,
                identity=IDENTITY,
                fingerprint=fingerprint,
            )
    finally:
        _restore_writable(root)


def test_staging_prefix_relocation_is_normalized(tmp_path: Path) -> None:
    final = tmp_path / f"cpython-3.12-{'a' * 64}"
    stage = tmp_path / f".{final.name}.stage-owned"
    (stage / "bin").mkdir(parents=True)
    script = stage / "bin/check-tool"
    script.write_text(
        f"#!{stage}/bin/python\nprint({str(stage)!r})\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    (stage / "bin/python").symlink_to("/usr/bin/python3.12")
    (stage / "pyvenv.cfg").write_text(
        f"command = /usr/bin/python3.12 -m venv {stage}\n",
        encoding="utf-8",
    )
    site_packages = stage / "lib/python3.12/site-packages"
    dist_info = site_packages / "demo-1.0.dist-info"
    dist_info.mkdir(parents=True)
    (site_packages / "demo.py").write_text("VALUE = 1\n", encoding="utf-8")
    record = dist_info / "RECORD"
    record.write_text(
        "demo-1.0.dist-info/RECORD,,\n"
        "demo.py,,\n"
        "missing/nested/generated.pyc,,\n"
        "../../../bin/check-tool,,\n",
        encoding="utf-8",
    )

    development.normalize_relocated_environment(stage, final)

    assert str(stage) not in script.read_text(encoding="utf-8")
    assert script.read_text(encoding="utf-8").startswith(f"#!{final}/bin/python")
    assert str(final) in (stage / "pyvenv.cfg").read_text(encoding="utf-8")
    assert record.read_text(encoding="utf-8").count("sha256=") == 2


def test_binary_staging_prefix_reference_is_rejected(tmp_path: Path) -> None:
    final = tmp_path / f"cpython-3.12-{'b' * 64}"
    stage = tmp_path / f".{final.name}.stage-owned"
    (stage / "bin").mkdir(parents=True)
    (stage / "bin/python").symlink_to("/usr/bin/python3.12")
    (stage / "embedded.bin").write_bytes(b"binary\x00" + os.fsencode(str(stage)))

    with pytest.raises(
        development.DevelopmentEnvironmentError,
        match="binary staging-prefix",
    ):
        development.normalize_relocated_environment(stage, final)


def test_worktree_venv_symlink_is_replaced_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    old = tmp_path / "old-environment"
    final = tmp_path / "new-environment"
    (config.repository_root / ".venv").symlink_to(old, target_is_directory=True)
    calls: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def observe_replace(source: os.PathLike[str], destination: os.PathLike[str]) -> None:
        source_path = Path(source)
        assert source_path.is_symlink()
        calls.append((source_path, Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(development.os, "replace", observe_replace)

    development.replace_worktree_environment_link(config, final)

    assert len(calls) == 1
    assert calls[0][1] == config.repository_root / ".venv"
    assert os.readlink(config.repository_root / ".venv") == str(final)
    assert not list(config.repository_root.glob(".venv.link-*"))


def test_checkpoint_generated_state_and_host_tools_are_isolated(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    environment = config.environment_root / "expected"
    paths = development.CheckpointPaths.beneath(
        config.repository_root / "tmp" / "checkpoint-test"
    )
    result = development.checkpoint_environment(
        {
            "ADB_DEVICE": "production-target",
            "ANDROID_SERIAL": "production-target",
            "PYTHONPATH": "/production/source",
            "THETOWER_ADB_PORT": "5555",
        },
        environment=environment,
        paths=paths,
    )

    for key in ("ADB_DEVICE", "ANDROID_SERIAL", "PYTHONPATH", "THETOWER_ADB_PORT"):
        assert key not in result
    isolated_values = (
        result["PYTHONPYCACHEPREFIX"],
        result["COVERAGE_FILE"],
        result["TOWER_ACTION_LOG_PATH"],
        result["THETOWER_DEVELOPMENT_SCREENSHOT_DIR"],
        result["THETOWER_STRATEGY_PROFILE_DIR"],
        result["THETOWER_DEVELOPMENT_SCRATCH_DIR"],
    )
    assert all(
        development._path_is_within(Path(value), config.repository_root)
        for value in isolated_values
    )
    assert str(config.production_environment) not in json.dumps(result, sort_keys=True)
    assert result["PATH"].startswith(f"{paths.blocked_tools}:")
    assert result["THETOWER_CHECKPOINT_EXCLUDE_HOST_TOOLS"] == "1"


def test_checkpoint_commands_include_only_offline_maintained_validators(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    environment = config.environment_root / "expected"
    paths = development.CheckpointPaths.beneath(tmp_path / "state")

    commands = development.checkpoint_commands(config, environment, paths)
    rendered = [" ".join(command) for _, command in commands]

    assert [label for label, _ in commands] == [
        "compile",
        "state definitions",
        "clickmap integrity",
        "pytest",
    ]
    assert any("test/validate_state_defs.py" in command for command in rendered)
    assert any("test/clickmap_integrity.py --show-orphans" in command for command in rendered)
    assert any("-p tools.development_pytest" in command for command in rendered)
    assert all("adb" not in command.lower() for command in rendered)
    assert all(
        not (len(command) == 2 and command[1] == "main.py")
        for _, command in commands
    )


def test_checkpoint_stops_and_propagates_failing_exit_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    environment = config.environment_root / "expected"
    return_codes = iter((0, 9, 0))
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, next(return_codes))

    result = development.run_checkpoint(config, environment, run=fake_run)

    assert result == 9
    assert len(calls) == 2
    assert "FAILED state definitions (exit 9)" in capsys.readouterr().err
