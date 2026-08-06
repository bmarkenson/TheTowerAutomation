# Runtime Evidence and Retention

## Evidence routes

- Actions/current state: `logs/actions.log`
- Persistent intent: `logs/automation_ctl.json`
- Stable latest frame and advisory metadata: `screenshots/latest.png` and
  `screenshots/latest.json`
- Battle/Tournament records: `logs/battles/` and `logs/tournaments/`
- No Strategy pages: `logs/battle_observations/<battle-id>/perk_configuration/`
- Failure/OCR captures: `screenshots/matches/`
- Canonical regression fixtures: `test/fixtures/`
- Active issue routing: `docs/observed_issues.md`; dossiers/history:
  `docs/issues/`

Generated artifacts are evidence, not current-state authority. Stable guidance
must not embed volatile process, target, control, wave, or screen claims.

## Completed-record discard

**Discard selected...** moves one confirmed Battle/Tournament JSON and Markdown
pair into `logs/discarded_battles/<discarded-at>__<battle-id>/`. Its
`discard.json` records source, time, and purge deadline; the default is 30 days
and `--discarded-battle-retention-days` changes it. A six-hour service loop and
ordinary history reads purge only valid expired packages.

Before expiry, restore the pair to the source directory named in
`discard.json`, confirm it reappears in Battle History, then remove leftover
quarantine metadata. Malformed or partial packages fail closed and are not
automatically purged.

## Generated-evidence sweeps

At startup and every six hours, runtime sweeps `screenshots/matches/`,
`logs/battle_observations/`, and explicitly configured repository-local sample
directories. Defaults are 30 days and 1 GiB per tree. Age runs first; then the
oldest files older than five minutes are removed until size is bounded. The
sweep does not follow symlinked subtrees or include canonical records, other
screenshot trees, or `test/fixtures/`.

Override with positive integer `THETOWER_ARTIFACT_RETENTION_DAYS`,
`THETOWER_ARTIFACT_MAX_BYTES`, and
`THETOWER_RETENTION_SWEEP_INTERVAL_SECONDS`.

`config/protected_artifacts.txt` protects narrow repository-relative paths,
families, or directory trees. Protected bytes still count toward size. An
absent, unreadable, absolute, or parent-traversing manifest fails the complete
sweep closed. Before citing generated evidence durably, prefer a canonical
fixture or narrow tracked extract; otherwise add the exact cleanup-root path
under the rules in
[`documentation_maintenance.md`](../documentation_maintenance.md).

`logs/actions.log` and optional mission logs rotate independently at 16 MiB
with five backups by default. `TOWER_ACTION_LOG_MAX_BYTES` and
`TOWER_ACTION_LOG_BACKUP_COUNT` change those limits; rotation keeps one
operator-summary/diagnostic group together.
