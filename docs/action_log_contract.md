# Action-Log Contract

Read this contract when adding or changing runtime logging or an input
workflow. `logs/actions.log` is the complete chronological record; presentation
filters may shorten a view but never discard its ordering or diagnostic detail.

## Level contract

| Level | Use |
| --- | --- |
| `ACTION` | One human-readable What/Why notice before an operator-meaningful workflow's first input. Nested mechanics are not separate actions. |
| `RESULT` | One terminal disposition for each `ACTION`: completed, no-op, deferred, interrupted, or failed, with the most useful counts or observations. |
| `INPUT` | One tap, swipe, or press. Put coordinates, verification, dispatch mode, and retry mechanics in paired `DEBUG` detail. |
| `STATUS` | A periodic current-state snapshot, shown separately from operational activity while retained in complete history. |
| `WARN` | A persistent, unexpected degradation with operator impact. Emit on transition, rate-limit reminders, and record recovery. Expected negative searches and in-budget retries are not warnings. |
| `ERROR` / `FAIL` | A component operation or broader runtime boundary could not complete safely. A warning or error does not replace the workflow's `RESULT`. |
| `INFO` | General lifecycle detail outside the concise operational narrative. |
| `DEBUG` / `MATCH` / `STATE` | Internal decisions, coordinates, retries, detector evidence, and state transitions. |

Operational activity contains `ACTION`, `RESULT`, `WARN`, `ERROR`, and `FAIL`.
Diagnostics contains `INPUT`, `DEBUG`, `MATCH`, and `STATE`; All Levels keeps
complete ordering. When a paired `ACTION` and `RESULT` share an operation ID,
the default view may fold the completed pair into the result while pending work
still shows its action.

## Workflow ownership

Low-level helpers return structured outcomes and keep ordinary retry detail
diagnostic. The workflow owner decides whether the result is a no-op, failure,
or persistent degradation. Every guarded or multi-step input route logs one
intent before input and one terminal result on every exit path. Audit-write
failure blocks an input that requires that audit; uncertain input is never
replayed automatically.

Keep status, activity-scope identity, and subsystem state machines in their
own canonical architecture. This document owns logging roles and pairing, not
runtime behavior.
