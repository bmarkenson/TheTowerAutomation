# Runtime Operations

This file is the routing index for live and production procedures. Before any
process or device interaction, runtime diagnosis, live validation, or claim
about volatile state, complete [`live_preflight.md`](live_preflight.md) once at
the current runtime, target, control, screen, and task boundary if it is not
already complete. Arriving here from that preflight does not restart it. Then
load only the operation that matches the task.

| Need | Canonical procedure |
| --- | --- |
| Promote one candidate or roll production back | [`operations/production_promotion.md`](operations/production_promotion.md) |
| Start, resume, or resolve a startup decision | [`operations/startup_gates.md`](operations/startup_gates.md) |
| Attach, reload, switch ADB targets, or change Strategy | [`operations/managed_runtime.md`](operations/managed_runtime.md) |
| Pause, change mode or game speed, replace a process, or recover a terminal run | [`operations/process_control.md`](operations/process_control.md) |
| Start a bounded read-only live stream for operator or worker observation | [`operations/passive_stream.md`](operations/passive_stream.md) |
| Hold automation for development or send one leased exact-target input | [`operations/interactive_development.md`](operations/interactive_development.md) |
| Run the No Strategy observation profile | [`operations/no_strategy.md`](operations/no_strategy.md) |
| Validate or passively observe an active Tournament | [`operations/tournament_validation.md`](operations/tournament_validation.md) |
| Operate the natural-boundary player-save audit collector | [`operations/player_save_audit.md`](operations/player_save_audit.md) |
| Retain, discard, or sweep generated runtime evidence | [`operations/evidence_retention.md`](operations/evidence_retention.md) |
| Decide whether an input, Surrender, Exit Battle, test battle, or Pause is authorized | [`live_action_authority.md`](live_action_authority.md) |
| Implement or review operator-facing action logs | [`action_log_contract.md`](action_log_contract.md) |
| Interpret host processes, locks, systemd, ADB, sockets, or long-lived commands | the matching section of [`sandbox_boundaries.md`](sandbox_boundaries.md) |
| Install or inspect the Linux user services | [`../deploy/systemd/README.md`](../deploy/systemd/README.md) |
| Publish, connect, or validate the native Windows client and telemetry | [`../windows/TheTower.ControlSurface/README.md`](../windows/TheTower.ControlSurface/README.md) |

Architecture and operating procedure have separate owners. Load a matching
section of [`architecture/runtime.md`](architecture/runtime.md) or
[`architecture/control_surface.md`](architecture/control_surface.md) only when
the task needs the underlying contract, schema, or authority boundary.

## Compatibility routes

The headings below preserve stable inbound links while directing readers to
the single current owner. Do not expand them into duplicate runbooks.

## Python and repository

Use the repository-change and development-environment paths in
[`new_thread.md`](new_thread.md).

## Production promotion and rollback

Use [`operations/production_promotion.md`](operations/production_promotion.md).

## ADB access

Use the exact-target checks in [`live_preflight.md`](live_preflight.md) and the
ADB evidence rules in [`sandbox_boundaries.md`](sandbox_boundaries.md).

### On-demand passive stream

Use [`operations/passive_stream.md`](operations/passive_stream.md).

## Mandatory runtime inspection

Use [`live_preflight.md`](live_preflight.md).

## Action-log contract

Use [`action_log_contract.md`](action_log_contract.md).

## Pause, resume, and process replacement

Use [`operations/process_control.md`](operations/process_control.md).

### Interactive development hold is not Pause

Use [`operations/interactive_development.md`](operations/interactive_development.md).

#### Lease-aware exact-target input

Use [`operations/interactive_development.md`](operations/interactive_development.md#one-exact-target-input).

### Recoverable runtime failures do not Pause

Use [`operations/process_control.md`](operations/process_control.md#runtime-failure-policy).

### Review a startup-check advisory

Use [`operations/startup_gates.md`](operations/startup_gates.md).

### Native Windows control surface

Use [`../windows/TheTower.ControlSurface/README.md`](../windows/TheTower.ControlSurface/README.md).

#### Windows host-performance tracker

Use the [native-client operating guide](../windows/TheTower.ControlSurface/README.md)
and the [telemetry architecture](architecture/control_surface.md#windows-host-performance-telemetry).

### No Strategy run inventory

Use [`operations/no_strategy.md`](operations/no_strategy.md).

## Live-action authority

Use [`live_action_authority.md`](live_action_authority.md).

## Evidence and records

Use [`operations/evidence_retention.md`](operations/evidence_retention.md).

### Player-save natural-boundary audit collector

Use [`operations/player_save_audit.md`](operations/player_save_audit.md).

## Storage retention and completed-record discard

Use [`operations/evidence_retention.md`](operations/evidence_retention.md).
