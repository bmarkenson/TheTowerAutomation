# ✅ Completed Tasks Log

This document tracks completed architectural, tooling, and refactor tasks for the "The Tower" automation project. Once a task is finalized and no longer belongs in the active roadmap, it should be moved here for historical reference.

---

## 🧱 Refactor and Architecture

- Centralized all clickmap access through `get_clickmap()` in `core/clickmap_access.py`
- Removed legacy file `input_named.py` after migrating all usage to `clickmap_access`
- Migrated `clickmap.json` to `config/` and updated all references across tools and tests
- Renamed all clickmap variables to `clickmap` for consistency
- Removed `coords/` folder and redistributed:
  - `gesture_logger.py`, `tune_gesture.py` → `tools/`
  - `clickmap.json` → `config/`
  - `run_tune_gesture` → deleted (manual launch note)
- Refactored tools/crop_region.py and main.py to use get_and_save_screenshot from ss_capture (centralizing save logic)
- Updated tools/crop_region.py to correctly handle gesture logging (single click / swipe, then redraw window).  Also implemented scrolling within the crop window
- Centralized clickmap-backed matching in `core.matcher` around a structured
  `MatchResult`, cached template loading, and shared region resolution while
  preserving the detector and label compatibility profiles at their public APIs.
- Added a persisted Daily Gem scheduler keyed to UTC midnight, which is 17:00
  PDT and 16:00 PST. It invokes the existing Store handler without requiring a
  badge, defers until a safe Home/Running state, backs off failures, and records
  completed or confirmed-not-ready outcomes once per game day.
- Made the control file the sole authority for runtime pause state. Manual
  pauses are now indefinite and survive restarts until an explicit resume;
  removing the duplicate in-memory 15-minute expiry eliminates the window in
  which automation could resume while the persisted directive still said
  `PAUSED`.


---

## 🧪 Testing & Validation

- Verified no external references to `input_named.py`
- Confirmed no remaining hardcoded `coords/` paths after migration

### 2026-07-13 headless template workflow

- Added a dry-run-first template tool that separates the exact asset crop from
  its runtime search region, validates both current match profiles, accepts
  positive and negative fixtures, and emits candidate, annotated, and JSON
  review artifacts without requiring a desktop session.
- Added guarded atomic commit support with explicit consent for replacements,
  shared assets, and dimension changes while preserving unrelated clickmap
  fields.
- Reproduced the Home Store-badge asset from its canonical fixture pixel for
  pixel and verified its 52x52 crop at the expected location within the
  distinct 64x66 runtime search region.

### 2026-07-13 test-log isolation

- Added a runtime-overridable primary action-log path and configured pytest to
  use a unique `/tmp/thetower-pytest-*` log before test modules are imported.
- Verified targeted and full test runs leave the live `logs/actions.log` size
  and modification time unchanged while retaining synthetic logs for diagnosis.

### 2026-07-13 live automation validation

- Added and live-verified `--adb-port` support with default port 5555.
- Made GC the default strategy and added an exclusive new-run startup gate.
- Live-verified the startup order: EHLS first, EALS second, then the
  session-scoped Target Priority check; both skip boxes were visibly `Max`.
- Split Exit Battle into guarded `Surrender` and `Go Home` actions; live-tested
  that Go Home preserves and resumes the same run and Surrender reaches Game
  Stats.
- Repaired Round Stats scrolling with source-screen guards and true-edge
  detection; live-tested the complete Game Over capture flow.
- Split home/in-run Store navigation, retained red-badge availability as the
  trigger, added a home red-badge region, and live-tested inactive Daily Gems as
  a normal not-ready result.
- Captured the 17:00 PDT new-day transition. Two Daily Missions appeared
  immediately, but the Store badge did not appear until Daily Missions was
  opened and closed; toggling the in-run menu alone did not refresh it.
- Confirmed the Store badge persisted from the running screen to Home and into
  a new run. Added a distinct Home badge template, refreshed the stale Home
  `Battle` template, and added a canonical Home fixture for both matches.
- Opened Cards through the live navigation template on ADB port 5565 and
  captured the active fixed `GC` preset. Added a composite template containing
  both its label and green selection border, validated it against two live
  positives and two non-Cards negatives, and added a canonical Cards fixture.
  Repointed the legacy GCFarm secondary states from generic slot-border crops
  to their full identity templates so an active `GC` deck is no longer falsely
  reported as `CARDS_GCFARM_EARLY`.
- Captured Event Bots with `Farm` active and Guild Guardian with `Fetch`,
  `Summon`, and `Scout` equipped. Added separate stable screen guards and
  configuration templates, plus an offline three-screen GC evidence validator.
  Rejected selected-tab templates after same-menu negatives scored 0.985-0.996;
  the workflow instead verifies stable target-screen content after navigation.
- Captured Event Missions and Guild Members as same-parent negative fixtures.
  The Guild frame preserves the unclaimed glowing 250 contribution chest; no
  reward or configuration control was tapped.
- Used guarded Go Home navigation without ending the active run and captured
  Workshop with `Farm` selected. Added stable Workshop/Farm identity templates
  and classified the selected preset by its green border rather than a
  high-correlation full-card template; the four cyan inactive neighbors provide
  same-frame negative evidence. No Workshop preset was changed.
- Opened the in-run Damage detail panel through the left-side label rather than
  the upgrade purchase offset and captured its persistent `Percent Of Enemy
  Health` selector at `1E-22%`. Added a primary panel state, stable guard,
  read-only OCR, and guarded open/dismiss actions using ordinary settled ADB
  screenshots. The changing `94.80M` derived damage is not used as state, and
  neither adjustment arrow was tapped.
- Fixed the Daily Gems handler so the active card's `FREE` price is not mistaken
  for a cooldown and a claim already visible at Store entry does not trigger a
  redundant top-and-back scroll. Live-verified the active in-run Store route,
  claim, ad skip, return to the running game, and badge clearance; the no-scroll
  entry path has automated coverage.
- Fixed the Daily Gems cooldown exit so `NOT_READY` is returned only after the
  handler taps Return to Game. Failure to find that control now fails the probe
  instead of recording an incomplete Store visit as a successful daily check.
  Live-verified the repaired path on 2026-07-14: the cooldown was detected,
  Return to Game matched and tapped, and the resulting state was `RUNNING`.
- Prevented scheduled Daily Gem probes from preempting transitional Home
  screens. The automatic path now waits for `RUNNING`; the retained rare
  Home-origin Store route returns through the bottom Home selection and verifies
  `HOME_SCREEN`, while the in-run route verifies `RUNNING`. Live validation on
  port 5565 detected a manually claimed gem's cooldown, returned to the battle,
  and persisted UTC day `2026-07-15` as `not_ready`.
- Replaced generic per-tick EHLS/EALS searches with a dedicated exclusive,
  state-driven initializer. It uses fast label templates and upgrade geometry,
  detects the rectangular gold `Max` border directly, supports either or both
  upgrades beginning gold boxed, and defers wave OCR until purchasing is done.
  Purchase taps continue independently of capture latency; a continuously
  drained H.264 stream supplies current verification frames, with guarded raw
  capture as fallback. In the final fresh live regression, EHLS gold boxed at
  wave 20 and EALS at wave 30 in both final fresh regressions. Human `touch`
  markers recorded the first EALS dispatch 0.285 and 0.472 seconds after EHLS
  became visibly gold (tap completion at 0.742 and 0.748 seconds). Completion
  waves, EALS first-tap wave/time, total elapsed time, tap count, and failure
  reason are recorded.
- Restored optional pause expiry without restoring the split-brain timer race.
  A plain control-file pause remains indefinite; `pause --minutes N` persists
  its deadline, the supervisor mirrors that deadline in memory, and expiry
  persists `RUNNING` before allowing automation actions to resume. A failed
  control-file write leaves the process paused.

### 2026-07-14 architecture safety foundation

- Separated battle lifecycle from visible UI navigation. `GAME_OVER` and a
  verified Home `NEW_BATTLE` control now end the observed battle identity;
  Home `RESUME_BATTLE`, unknown Home evidence, and transient unknown screens
  preserve it. The existing Home OCR/template evidence is shared by lifecycle
  handling and guarded Home actions. A live guarded Go Home at wave 3457
  exposed the stale historical Resume asset, while OCR classified `RESUME
  BATTLE` at 93.75 confidence. The refreshed template matched the live frame at
  1.000 and stayed below threshold on the canonical new-Battle fixture; its
  guarded visible tap returned to the same battle at wave 3468. Replaying those
  live observations through the new lifecycle emitted no second run start. A
  later genuine Home boundary repeatedly classified `NEW_BATTLE` at 96.0 OCR
  confidence while paused without activating initialization. Its guarded
  visible tap started exactly one gate; EHLS completed at wave 20 and EALS at
  wave 30.
- Added a non-blocking OS process lock keyed by ADB target. A second runtime for
  the same target exits before constructing `App`, while different target ports
  retain independent lock files.
- Separated legacy direct-`match_region` center resolution from runtime blind
  input authority. `get_click()` retains its historical center behavior for
  compatibility and tooling, while blind named `safe_tap` actions require an
  explicit `tap`. Broad scrolling `region_ref` windows continue to locate and
  tap the actual matched element; the four in-run menu navigation targets now
  declare their existing static coordinates explicitly.
- Repaired the paused exclusive startup gate. Initialization ownership now
  follows the active battle lifecycle across transient unknown frames, paused
  capture/detection/status reporting remains active without actions, and gate
  completion is logged only after the strategy assertion succeeds. The
  49-test architecture checkpoint and live paused/resumed validation both
  passed; the live level skips required zero purchase taps and Target Priority
  was verified before the gate released.

### 2026-07-15 GC strategy profiles

- Replaced the tactical `target_priority_checked=True` strategy variant with a
  concrete build-time GC family/profile model. `gc_farm_t18` enforces its
  explicit Target Priority order, while `gc_farm_t19_experiment` omits Target
  Priority from both the generated action rules and startup completion gate.
- Routed profile-provided orders through the shared action executor to the
  existing Target Priority enforcer. Failed verification leaves the gate
  incomplete; the successful session-scoped result persists across run
  boundaries.
- Retained `gc_manual_target_priority` only as a compatibility name resolving
  to the explicit Tier 19 generated profile, with no strategy-name conditional
  in the app and no seeded completion state.

### 2026-07-15 GC session preflight

- Added a generic post-initialization session gate and profile-carried GC
  requirements. Completion persists across run boundaries in one process;
  paused and transient-unknown observations cannot release or act through the
  gate.
- Added guarded read-only traversal and evidence for GC Cards, Farm Workshop,
  Farm Bots, Fetch/Summon/Scout Guardian chips, Auto Pick Perks, and all nine
  required Ultimate Weapons. Dedicated Perks-close and visible Home
  Event/Guild templates replaced broken generic/static dependencies.
- Live-validated a natural Tier 19 wave-2558 Game Over -> Retry boundary, EHLS
  then EALS at waves 20/30, preserve-mode Target Priority with no action, and a
  complete once-per-session preflight. Mismatches block and log evidence;
  automatic correction and Surrender remain disabled.

### 2026-07-15 Mission and Guild reward collection

- Added a bounded side-menu reward probe. The aggregate red/purple attention
  dot schedules inspection but never authorizes a reward tap; fixed Daily,
  Event, and Guild badge regions select panels, and every action requires a
  fresh parent-state check plus exact available artwork.
- Added distinct positive/negative evidence for Daily mission claims, weekly
  chests, Event mission claims, and Guild contribution chests. Claimed and
  locked chest artwork stays below threshold. Weekly/Guild reward reveals share
  the verified `SKIP` control; Event scanning uses screen-guarded bounded
  scrolling.
- Live-validated the full handler on a paused Tier 20 run at port 5565. It
  claimed three remaining Daily missions and one remaining Event reward,
  skipped Guild because only claimed/locked chests were present, logged
  `daily=3 event=1 guild=0`, and restored `RUNNING/MENU_CLOSED`. A second probe
  saw no relevant badges despite an unrelated Modules badge and performed no
  reward action. The battle continued naturally and was never surrendered.

---

## 📘 Documentation

- Created `core/input_policy.md` to document dual-path tap architecture
- Updated `README_UPLOAD.md` with summary of input tap architecture and assistant behavior
- Updated `PROJECT_SCOPE.md` to reflect dual-path tap architecture, overlay support, and tap handler split
