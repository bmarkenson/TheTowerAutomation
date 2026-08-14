# Runtime Operations

This file is the routing index for live and production procedures. Before any
process or device interaction, runtime diagnosis, live validation, or claim
about volatile state, complete [`live_preflight.md`](live_preflight.md) unless
it is already complete for the current task boundary. Then load only the
matching operation.

| Need | Canonical procedure |
| --- | --- |
| Promote one candidate or roll production back | [`operations/production_promotion.md`](operations/production_promotion.md) |
| Start, resume, or resolve a startup decision | [`operations/startup_gates.md`](operations/startup_gates.md) |
| Start/stop, attach, switch ADB targets, or change Strategy | [`operations/managed_runtime.md`](operations/managed_runtime.md) |
| Pause, change mode or game speed, replace a process, or recover a terminal run | [`operations/process_control.md`](operations/process_control.md) |
| Start a bounded read-only live stream for operator or worker observation | [`operations/passive_stream.md`](operations/passive_stream.md) |
| Hold automation for development or send one leased exact-target input | [`operations/interactive_development.md`](operations/interactive_development.md) |
| Run the No Strategy observation profile | [`operations/no_strategy.md`](operations/no_strategy.md) |
| Validate or passively observe an active Tournament | [`operations/tournament_validation.md`](operations/tournament_validation.md) |
| Enable, inspect, or stop a bounded player-save temporal-audit campaign | [`operations/player_save_audit.md`](operations/player_save_audit.md) |
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

## ADB access

This heading preserves one historical inbound link. Current readers use the
table above.

Use the exact-target checks in [`live_preflight.md`](live_preflight.md) and the
ADB evidence rules in [`sandbox_boundaries.md`](sandbox_boundaries.md).
