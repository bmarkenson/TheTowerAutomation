# Architecture Direction and Thread Handoff — 2026-07-14

> Historical review record. Current runtime architecture is maintained in
> [`../runtime.md`](../runtime.md); active work is indexed in
> [`../../../PENDING_DEVELOPMENT.md`](../../../PENDING_DEVELOPMENT.md).

This note is the durable outcome of the architecture-review thread that began
by asking whether the clickmap paradigm is sound. `PENDING_DEVELOPMENT.md`
remains the canonical task list; this document records the reasoning and scope
so a new thread does not need the compacted conversation history.

## Original mandate

The thread's purpose was to evaluate the primary automation architecture, make
a small safety foundation at a natural development pause, and validate those
changes against the live instance while automation remained paused. It was not
intended to redesign every handler, build a floating-gem detector, or continue
unrelated GC feature development.

## Earlier thread baseline and supersession

The architecture review began from the following committed baseline:

- `f43edef` — Restore safe timed pause support
- `0728532` — Make runtime pauses indefinite
- `16577ea` — Document floating gem tracking evidence
- `bdf485c` — Add read-only Damage adjuster inspection
- `c79fc58` — Add Workshop Farm preflight evidence

Use `/home/brianm/dev/python/TheTower` as the repository root,
`.venv/bin/python` for Python commands, and ADB port 5555 unless the live
process was explicitly started for another target. Preserve unrelated modified
and untracked files, report behavioral blockers instead of weakening guards,
and keep changes in focused incremental commits after the current dirty package
has been reviewed and separated deliberately.

The earlier handoff named the next GC state-validation package as the immediate
task. That priority was superseded when the architecture review exposed the
unsafe Home/run-boundary model and, after repairing that model, the paused
startup-gate defect. GC validation remains pending after the startup gate is
fixed. In particular, the observed Damage value `1E-22%` has not been confirmed
as the desired GC setting and must not be encoded as policy yet.

The earlier warning that Go Home/Resume could be mistaken for a new run is also
superseded: the battle-lifecycle separation described below fixes that model,
and both Resume and genuine Home `NEW_BATTLE` paths have been live-validated.

## Architecture decision

The clickmap is a useful part of the architecture, but it should not be the
architecture's control plane. It is best treated as a declarative catalog of UI
facts:

- template identity, search geometry, thresholds, and roles;
- explicit static tap/swipe geometry where an action genuinely requires it;
- shared regions used to find elements that move within a scroll window.

The clickmap should not own current UI state, battle identity, action ordering,
pause semantics, retries, handler ownership, or recovery policy. Those belong
in separate layers:

| Layer | Responsibility |
| --- | --- |
| Capture/observation | Produce fresh frames with sequence and timing metadata. |
| Matching/clickmap | Describe and locate visible UI evidence. |
| Semantic state | Interpret evidence as primary state, overlays, and lifecycle events. |
| Orchestration/policy | Decide which component may act and in what order. |
| Action authority | Recheck guards and issue a visible-element or explicit static action. |
| Feature handlers | Implement one bounded game behavior through the shared layers. |

This keeps the current clickmap investment while preventing coordinate data
from silently becoming state or action authority.

### Match-region centers are intentionally retained

The architecture work did not remove match-region centers. They remain useful
when a visible element moves inside a scrolling search window: the matcher
searches the configured region and the visibility-aware action taps the center
of the element's actual matched bounding box.

The safety distinction is narrower. A broad search region is not, by itself,
permission to make a blind tap at that region's center. Runtime blind named
actions now require an explicit `tap`. The legacy `get_click()` direct-region
center behavior remains available for compatibility and tooling, and shared
`region_ref` windows continue to support moving-element matching.

## Safety foundation completed in this thread

- Battle lifecycle is separate from visible navigation. Home `RESUME_BATTLE`
  preserves battle identity, while `GAME_OVER` or a verified Home `NEW_BATTLE`
  ends it. Home classification is shared by lifecycle handling and the guarded
  Home action instead of being reimplemented in both places.
- A non-blocking, per-ADB-target process lock prevents two runtimes from sending
  competing actions to the same device.
- Blind action authority is separated from legacy region-center lookup.
  Visibility-aware actions still use the actual match; static blind actions
  require explicit action geometry.
- The stale Home Resume template was refreshed and live-validated. A guarded
  Resume returned to the same battle, and replaying the observations did not
  emit a second run start. At a later genuine boundary, repeated paused Home
  observations classified `NEW_BATTLE` at 96.0 OCR confidence without arming
  initialization; the guarded Battle tap then started exactly one gate. EHLS
  completed at wave 20 and EALS at wave 30.

The duplicate-implementation audit found no accidental second implementation
of these new responsibilities. The lifecycle tracker, Home classifier,
single-instance lock, canonical matcher delegation, and explicit/legacy
clickmap split each have one active implementation. Smaller pre-existing
duplication remains in screenshot wrappers, atomic JSON writers, polling loops,
level-skip navigation helpers, and a developer scrolling tool; consolidate
those only with call-site and behavior evidence, not as part of this handoff.

At the completed architecture checkpoint, 49 targeted tests passed. That result
is evidence for the checkpoint, not a substitute for rerunning tests after
future changes.

## Startup-gate defect and resolution

The restarted live process exposed a paused exclusive-gate defect: an early
`continue` suppressed normal status reporting, and
`run_initialization_pending()` treated a non-`RUNNING` frame as completion even
when the strategy assertions remained false.

The fix binds initialization ownership to the observed battle lifecycle rather
than the current primary frame. A transient `UNKNOWN` therefore preserves an
incomplete gate. While paused or exclusively gated, capture, detection,
lifecycle observation, and read-only status reporting continue, while mission,
strategy, overlay, recovery, primary-handler, coin-toggle, and blind-tapper
actions are blocked. The completion message is additionally guarded by the
strategy's actual initialization-completion assertion.

Regression coverage exercises paused startup, read-only status reporting, and
`RUNNING -> UNKNOWN -> RUNNING` while incomplete. Live validation started the
updated runtime under authoritative `PAUSED` control and observed two status
cycles with advancing waves and no actions. After explicit authorization to
resume, EHLS and EALS were confirmed gold boxed with zero purchase taps, Target
Priority was verified, and only then was "Startup gate complete" logged. The
runtime was stopped cleanly afterward and the control file was left `PAUSED`.

## Floating gem (Bob) conclusion

Bob investigation was useful evidence but was scope drift. The proven scheduled
bottom-intercept tapping remains the behavioral baseline. Its immediate safety
weakness is not timing; it checks the automation control state rather than a
freshly verified on-screen `RUNNING` state.

Single-frame Bob recognition is not currently reliable among combat effects.
The directional templates and color heuristic produced too many misses or
competing matches, and a multi-frame track needed a long observation window.
The user confirmed that Bob's speed is static. Apparent speed variation in the
experiment came from retrieval-time timestamps, buffering/skipped stream
sequences, and human-selected positions; those measurements must not be used as
game-motion evidence. The approximate circular path geometry is useful, but it
does not prove automatic detectability.

The original capture provenance is still available as of this handoff, but it
lives under `/tmp` and is not durable:

- `/tmp/thetower_floating_gem_stream_20260714` is the valid 69-frame positive
  H.264 burst.
- `/tmp/thetower_floating_gem_full_orbit_20260714` must be treated only as a
  noisy no-Bob negative. The former pause-timer race may have allowed automation
  to collect Bob during that 112-frame capture.

Do not silently reinterpret the second directory as a complete positive orbit.
If these captures become necessary for an implemented detector or regression
test, first promote a reviewed, minimal fixture set into durable repository
assets with its positive/negative provenance recorded.

The first improvement should therefore preserve the working blind cadence and
add a cheap, fresh authorization check:

- an app-owned observer publishes an atomic UI-state snapshot containing state,
  frame sequence, observation time, and an invalidation epoch;
- the tapper performs an O(1) in-memory check immediately before each scheduled
  tap and acts only under a fresh `RUNNING` lease while automation is unpaused;
- navigation, a non-running frame, capture failure, pause, or staleness
  invalidates the lease;
- the schedule uses an absolute monotonic cadence (`next_tap_at += interval`),
  so a skipped or guarded tap does not shift every future intercept.

Do not add a second competing `screenrecord` process solely for this guard. The
state lease belongs with the deferred app-owned frame-source design, including
the device's 180-second recording limit, restart/handoff semantics, and stale
frame rejection. Bob tracking can remain optional research after that safety
layer exists; it is not a prerequisite for retaining the working tap method.

## Scope for the next work

1. Run the default GC strategy through a natural Game Over -> Retry boundary to
   confirm that the exclusive startup gate repeats EHLS then EALS while Target
   Priority remains session-scoped.
2. Treat the app-owned frame source plus short-lived UI-state action lease as a
   separate, reviewable architecture package. Do not begin with a Bob detector.

Matcher-policy consolidation, the centralized handler registry, GC session
preflight, and general duplicate-helper cleanup remain valid later work, but
they are outside the immediate handoff.
