# Unconfirmed Issue History — 2026

This history preserves complete reports that remain unreproduced and are not
currently actionable. Entries here are neither active routing items nor
resolved issues. Load one only when a matching observation recurs; then retain
the evidence requested by its dossier before refreshing or changing behavior.

Current active routing lives in
[`../observed_issues.md`](../observed_issues.md), and resolved history lives in
[`resolved-2026.md`](resolved-2026.md).

## Unconfirmed observations

### Startup gate dialog was reported without visible Retry or Bypass choices

**Stable ID:** `ISSUE-2026-006` · **Lifecycle:** `unconfirmed_non_actionable` · **Routing class:** `unconfirmed_history`

- **Observed:** Operator report on 2026-07-23 while the blocking
  `free_upgrade_locks` decision was pending.
- **Symptom:** The browser dialog said that the Startup Gate needed direction,
  but the operator initially could not see a Retry or Bypass choice and
  wondered whether an older GUI was running.
- **Evidence:** The persisted directive created at 02:20:55 contained both
  `retry` and `bypass_once` options. At 02:48:28 the action log records that
  the control surface active at that time resolved the same request with
  `retry`, and the runtime consumed it at 02:48:33. Static assets were served
  with `no-cache` and API status with `no-store`, so available evidence did not
  establish an older browser bundle.
- **Safety response:** No option was chosen during diagnosis. The later Retry
  used the normal requirement path and did not create a waiver.
- **Status:** Unconfirmed transient display problem. The backend choices and
  end-to-end resolution path are covered by
  `test/test_automation_control.py` and `test/test_control_surface.py`. If it
  recurs, retain a browser screenshot and viewport dimensions before
  refreshing so the rendering failure can be distinguished from stale content
  or an off-screen dialog.
