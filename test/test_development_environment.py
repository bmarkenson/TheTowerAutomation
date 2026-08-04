from __future__ import annotations

from dataclasses import replace
import json
import multiprocessing
import os
from pathlib import Path
import shutil
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
    config_path.write_text('{"bootstrap_schema":3}\n', encoding="utf-8")
    for relative in inputs:
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"input={relative}\n", encoding="utf-8")
    return development.EnvironmentConfig(
        repository_root=repository,
        config_path=config_path,
        bootstrap_schema=3,
        dependency_inputs=inputs,
        environment_root=worktrees / ".environments",
        production_environment=tmp_path / "production" / ".venv",
        interpreter=Path("/usr/bin/python3.12"),
        supported_identity=IDENTITY,
        runtime_root=tmp_path / "runtime" / "thetower",
    )


def _fingerprint(
    config: development.EnvironmentConfig,
) -> development.EnvironmentFingerprint:
    return development.compute_environment_fingerprint(config, IDENTITY)


def _final(
    config: development.EnvironmentConfig,
    fingerprint: development.EnvironmentFingerprint,
) -> Path:
    return development.expected_environment_path(
        config,
        IDENTITY,
        fingerprint.digest,
    )


def _write_marker(
    config: development.EnvironmentConfig,
    fingerprint: development.EnvironmentFingerprint,
    final: Path,
) -> None:
    development.write_completion_marker(
        final,
        config=config,
        identity=IDENTITY,
        fingerprint=fingerprint,
    )


def _verify_test_payload(
    final: Path,
    _config: development.EnvironmentConfig,
    _identity: development.InterpreterIdentity,
    _fingerprint: development.EnvironmentFingerprint,
) -> None:
    assert (final / "payload.txt").read_text(encoding="utf-8") == "complete\n"


def _lock_worker(
    config: development.EnvironmentConfig,
    entered: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
) -> None:
    with development.development_writer_lock(config):
        entered.set()
        release.wait(5)


def _copy_lock_contract(tmp_path: Path) -> development.EnvironmentConfig:
    config = _config(tmp_path)
    source_root = development.repository_root()
    for relative in (
        "pyproject.toml",
        "requirements/bootstrap.in",
        "requirements/bootstrap.lock",
        "requirements/runtime.lock",
        "requirements/development.lock",
    ):
        destination = config.repository_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / relative, destination)
    return config


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


def test_production_checkout_interpreter_and_venv_target_are_rejected(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)

    with pytest.raises(
        development.DevelopmentEnvironmentError,
        match="production checkout",
    ):
        development.validate_repository_root(
            replace(config, repository_root=config.production_environment.parent)
        )

    with pytest.raises(
        development.DevelopmentEnvironmentError,
        match="cannot come from production",
    ):
        development.validate_repository_root(
            replace(
                config,
                interpreter=config.production_environment / "bin/python",
            )
        )

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


def test_final_path_must_be_the_exact_fingerprinted_store_child(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    fingerprint = _fingerprint(config)
    final = _final(config, fingerprint)

    development.validate_final_path(config, IDENTITY, fingerprint.digest, final)
    with pytest.raises(development.DevelopmentEnvironmentError, match="does not match"):
        development.validate_final_path(
            config,
            IDENTITY,
            fingerprint.digest,
            config.environment_root / "another-child",
        )


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


def test_marker_absent_interrupted_build_is_rebuilt_without_touching_siblings(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    fingerprint = _fingerprint(config)
    final = _final(config, fingerprint)
    config.environment_root.mkdir(mode=0o700)
    other = config.environment_root / f"cpython-3.12-{'f' * 64}"
    other.mkdir()
    (other / development.COMPLETION_MARKER_NAME).write_text(
        "preserved completed environment\n",
        encoding="utf-8",
    )
    events: list[str] = []

    def interrupted_build(
        _config: development.EnvironmentConfig,
        _identity: development.InterpreterIdentity,
        _fingerprint: development.EnvironmentFingerprint,
        environment: Path,
    ) -> None:
        (environment / "partial.txt").write_text("partial\n", encoding="utf-8")
        raise RuntimeError("injected interruption")

    with pytest.raises(RuntimeError, match="injected interruption"):
        development.provision_environment(
            config,
            IDENTITY,
            fingerprint,
            build=interrupted_build,
            verify_contents=_verify_test_payload,
        )
    assert (final / "partial.txt").is_file()
    assert not (final / development.COMPLETION_MARKER_NAME).exists()

    def completed_build(
        _config: development.EnvironmentConfig,
        _identity: development.InterpreterIdentity,
        _fingerprint: development.EnvironmentFingerprint,
        environment: Path,
    ) -> None:
        events.append("build")
        assert not (environment / "partial.txt").exists()
        assert not (environment / development.COMPLETION_MARKER_NAME).exists()
        (environment / "payload.txt").write_text("complete\n", encoding="utf-8")

    def verify_before_marker(
        environment: Path,
        environment_config: development.EnvironmentConfig,
        identity: development.InterpreterIdentity,
        environment_fingerprint: development.EnvironmentFingerprint,
    ) -> None:
        events.append("verify")
        assert not (environment / development.COMPLETION_MARKER_NAME).exists()
        _verify_test_payload(
            environment,
            environment_config,
            identity,
            environment_fingerprint,
        )

    result = development.provision_environment(
        config,
        IDENTITY,
        fingerprint,
        build=completed_build,
        verify_contents=verify_before_marker,
    )

    assert result.reused is False
    assert events == ["build", "verify"]
    development.verify_completion_marker(
        final,
        config=config,
        identity=IDENTITY,
        fingerprint=fingerprint,
    )
    assert (other / development.COMPLETION_MARKER_NAME).read_text(
        encoding="utf-8"
    ) == "preserved completed environment\n"
    assert os.readlink(config.repository_root / ".venv") == str(final)


def test_completed_valid_environment_is_reused_without_building(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    fingerprint = _fingerprint(config)
    final = _final(config, fingerprint)
    final.mkdir(parents=True)
    (final / "payload.txt").write_text("complete\n", encoding="utf-8")
    _write_marker(config, fingerprint, final)
    before = {
        path.name: path.read_bytes()
        for path in final.iterdir()
        if path.is_file()
    }

    def no_build(*_args: object) -> None:
        raise AssertionError("a completed valid environment must be reused")

    result = development.provision_environment(
        config,
        IDENTITY,
        fingerprint,
        build=no_build,
        verify_contents=_verify_test_payload,
    )

    after = {
        path.name: path.read_bytes()
        for path in final.iterdir()
        if path.is_file()
    }
    assert result.reused is True
    assert before == after
    assert os.readlink(config.repository_root / ".venv") == str(final)


def test_completed_invalid_environment_is_reported_without_mutation(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    fingerprint = _fingerprint(config)
    final = _final(config, fingerprint)
    final.mkdir(parents=True)
    broken = final / "payload.txt"
    broken.write_text("broken\n", encoding="utf-8")
    _write_marker(config, fingerprint, final)
    before = {path.name: path.read_bytes() for path in final.iterdir()}

    with pytest.raises(
        development.DevelopmentEnvironmentError,
        match="Completed development environment.*refusing to modify",
    ):
        development.provision_environment(
            config,
            IDENTITY,
            fingerprint,
            build=lambda *_args: pytest.fail("completed environment was rebuilt"),
            verify_contents=_verify_test_payload,
        )

    assert {path.name: path.read_bytes() for path in final.iterdir()} == before


def test_worktree_venv_selection_is_atomic_and_preserves_real_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    old = tmp_path / "old-environment"
    final = tmp_path / "new-environment"
    link = config.repository_root / ".venv"
    link.symlink_to(old, target_is_directory=True)
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
    assert calls[0][1] == link
    assert os.readlink(link) == str(final)
    assert not list(config.repository_root.glob(".venv.link-*"))

    link.unlink()
    link.mkdir()
    with pytest.raises(
        development.DevelopmentEnvironmentError,
        match="Refusing to replace non-symlink",
    ):
        development.replace_worktree_environment_link(config, final)
    assert link.is_dir() and not link.is_symlink()


def test_status_rejects_missing_and_incomplete_environments(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    fingerprint = _fingerprint(config)
    final = _final(config, fingerprint)

    assert development._print_status(config, IDENTITY, fingerprint) == 1
    assert "status=missing" in capsys.readouterr().out

    final.mkdir(parents=True)
    with pytest.raises(
        development.IncompleteEnvironmentError,
        match="incomplete.*completion marker is absent",
    ):
        development._print_status(config, IDENTITY, fingerprint)


def test_status_rejects_mismatched_marker_selection_and_broken_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    fingerprint = _fingerprint(config)
    final = _final(config, fingerprint)
    final.mkdir(parents=True)
    marker = final / development.COMPLETION_MARKER_NAME
    marker.write_text(
        json.dumps(
            {
                "bootstrap_schema": config.bootstrap_schema,
                "environment_fingerprint": "0" * 64,
                "schema_version": development.COMPLETION_MARKER_SCHEMA,
            }
        ),
        encoding="utf-8",
    )
    (config.repository_root / ".venv").symlink_to(final, target_is_directory=True)

    with pytest.raises(
        development.DevelopmentEnvironmentError,
        match="Completed development environment.*does not match",
    ):
        development._print_status(config, IDENTITY, fingerprint)

    marker.unlink()
    _write_marker(config, fingerprint, final)
    monkeypatch.setattr(
        development,
        "verify_completed_environment",
        lambda *_args: None,
    )
    link = config.repository_root / ".venv"
    link.unlink()
    link.symlink_to(tmp_path / "wrong-environment", target_is_directory=True)
    with pytest.raises(development.DevelopmentEnvironmentError, match="targets"):
        development._print_status(config, IDENTITY, fingerprint)

    link.unlink()
    link.symlink_to(final, target_is_directory=True)

    def broken(*_args: object) -> None:
        raise development.DevelopmentEnvironmentError("missing bin/python")

    monkeypatch.setattr(development, "verify_completed_environment", broken)
    with pytest.raises(
        development.DevelopmentEnvironmentError,
        match="Completed development environment.*missing bin/python",
    ):
        development._print_status(config, IDENTITY, fingerprint)


def test_lock_verification_and_regeneration_are_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _copy_lock_contract(tmp_path)
    locks = development.validate_lock_inputs(config)
    before = {
        relative: (config.repository_root / relative).read_bytes()
        for relative in development.LOCK_SOURCES
    }

    def successful_command(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(development, "verify_interpreters", lambda _config: IDENTITY)
    monkeypatch.setattr(development, "_run_checked", successful_command)
    development.regenerate_locks(config)
    development.regenerate_locks(config)

    after = {
        relative: (config.repository_root / relative).read_bytes()
        for relative in development.LOCK_SOURCES
    }
    assert all(locks.values())
    assert after == before


def test_checkpoint_generated_state_is_isolated_while_host_tools_are_available(
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
            "PATH": f"{config.production_environment}/bin:/usr/bin",
            "PYTHONPATH": "/production/source",
            "THETOWER_ADB_PORT": "5555",
            "VIRTUAL_ENV": str(config.production_environment),
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
    assert result["PATH"] == f"{environment / 'bin'}:/usr/local/bin:/usr/bin:/bin"
    assert "THETOWER_CHECKPOINT_EXCLUDE_HOST_TOOLS" not in result


def test_checkpoint_commands_cover_full_offline_repository_gate(
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
    assert any(
        "test/clickmap_integrity.py --show-orphans" in command for command in rendered
    )
    pytest_command = commands[-1][1]
    assert pytest_command[:4] == (
        str(environment / "bin/python"),
        "-m",
        "pytest",
        "-q",
    )
    assert "tools.development_pytest" not in " ".join(pytest_command)
    assert all("adb" not in command.lower() for command in rendered)
    assert all(
        not (len(command) == 2 and command[1] == "main.py")
        for _, command in commands
    )


def test_checkpoint_stops_and_returns_failing_exit_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    environment = config.environment_root / "expected"
    return_codes = iter((0, 9, 0))
    calls: list[list[str]] = []

    def fake_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, next(return_codes))

    result = development.run_checkpoint(config, environment, run=fake_run)

    assert result == 9
    assert len(calls) == 2
    assert "FAILED state definitions (exit 9)" in capsys.readouterr().err
    checkpoint_parent = config.repository_root / "tmp" / "development-checkpoint"
    assert list(checkpoint_parent.iterdir()) == []
