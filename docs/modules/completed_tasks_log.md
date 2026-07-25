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

### 2026-07-25 Demon Mode/Nuke recharge activation preflight

- Added strategy-owned recharge activation defaults for Farm and Tournament:
  Demon Mode automatically activates when its recharge completes, while Nuke
  becomes available but waits for manual activation.
- Home `NEW_BATTLE` setup now locates both Cards in inventory, opens the exact
  detail through a guarded long press, classifies the checkbox from retained
  live evidence, leaves matching states untouched, and corrects and re-verifies
  only authoritative mismatches. Missing or ambiguous evidence fails closed.
- Live observation confirmed both card details describe a 300-wave recharge,
  with Demon Mode checked and Nuke unchecked. The observation did not change
  either checkbox or start a battle.
- Focused strategy, Home-gate, clickmap, reporting, and control coverage passed
  191 tests. Repository-wide validation passed 751 sandbox-compatible tests
  plus the separately permitted localhost HTTP test, for 752 total.
  Implemented in commit `7e542f4`.

### 2026-07-25 Range-selected Orb Distance enforcement

- Reproduced the Tournament validation failure against its exact live Attack
  frame: the Range tile was correctly located and visibly showed `98.38m`, but
  raw OCR returned no text for the dim Max-state value. One bounded
  adaptive-contrast retry now reads that frame as `98.38m` at 86% confidence.
- Generated Farm and Tournament actions now carry every configured Orb
  Distance preset. The authoritative observed Range selects its matching
  Extra/Workshop pair. A readable Range outside the configured set is retained
  as an operator experiment and completes without opening or changing Distance
  Adjuster; unreadable Range evidence still fails closed.
- The failed one-shot validation retained ownership through timeout, Surrender,
  Game Over, and verified Home cleanup. No battle remained after diagnosis.
- Focused Orb Distance and strategy-builder coverage passed 105 tests.
  Repository-wide validation passed 734 sandbox-compatible tests plus the
  separately permitted localhost HTTP test, for 735 total. Implemented in
  commit `3bc3ab4`.

### 2026-07-25 slot-level module replacement and exclusive-check skips

- Replaced Unequip-based Primary/Assist cycle handling with a verified
  same-family level-1 intermediate. Every occupied replacement now requires
  the game's level-transfer prompt, while filling a known empty recovery slot
  rejects an unexpected transfer prompt. The generated loadout remains generic
  and strategy-owned.
- Hardened inventory selection with aligned icon ranking, independent
  confidence and runner-up-margin authority, exact detail name/action/level
  checks, settled rarity-row reacquisition, and complete settled-overview
  validation.
- Repaired the interrupted Farm armor assignment live without losing either
  slot level: Anti-Cube Portal finished in armor Assist at level 194, Orbital
  Augment finished in armor Primary at level 201, and fresh overview evidence
  matched all eight configured Farm modules. Automation remained paused and no
  battle was started.
- Tournament exclusive validation now claims the staged, strategy-scoped
  one-run check waivers used by normal startup, so selecting **skip Modules**
  actually suppresses module work on that validation path.
- Focused regression coverage exercises direct transfers, level-1
  intermediates, unexpected prompts, settled filter rows, aligned candidates,
  confidence/margin refusal, and the exclusive-validation Module skip.
  Repository-wide validation passed 731 sandbox-compatible tests plus the
  separately permitted localhost HTTP test, for 732 total. Implemented in
  commits `859351f`, `1121bff`, `983e1f0`, and `4edc809`.

### 2026-07-25 Farm Perk configuration enforcement

- Promoted Perk Bans and Auto Pick priority into strategy-owned Farm
  invariants. The canonical order includes Coin Trade-Off at priority 3 and is
  expanded into both current Farm plans and the retained GC aliases.
- Added Home `NEW_BATTLE` OCR and guarded repair. Ban changes use matched
  Selected Perks rows to remove extras and search Available rows only for
  missing required bans. Ban repair completes before Auto Pick opens; Auto
  Pick then inserts each declared perk into its exact rank through freshly
  verified upward moves. Ambiguous identity, missing rows, unchanged inputs,
  non-progress, and bounded-search exhaustion all fail closed before battle.
- Live Farm T19 testing detected and corrected an extra Coin Trade-Off ban.
  The first revision took the longer Available-list route and exposed that the
  blocking Home workflow did not consume Pause. The follow-up synchronizes
  persistent control before every setup input; Pause is action-free and Resume
  restores Home before a fresh pass.
- The Auto Pick live retry also exposed row coordinates captured while the
  list was still settling after a swipe. Actions now recapture and uniquely
  reacquire the semantic row immediately before input, then rebuild rank from
  the top and require exactly one-rank progress. Live validation moved Coin
  Trade-Off from rank 29 to rank 3 through 26 proven steps and passed the exact
  13-entry final comparison.
- Retained July 22 Farm screenshots verify all five configured bans and all
  thirteen priorities. Automated coverage exercises the missing Coin
  Trade-Off repair, direct selected-ban removal, Ban-before-Auto sequencing,
  Pause/Stop authority, pre-action row drift, exact rank progress, strategy
  expansion, Home-gate integration, and No Strategy compatibility.
- Repository-wide follow-up validation passed 725 sandbox-compatible tests
  plus the separately permitted localhost HTTP test, for 726 total.
- Implemented in commits `bafeff4`, `c4cb745`, and `227465b`.

### 2026-07-25 Farm module preflight visibility and transitions

- Isolated each Modules rarity verifier from adjacent rows, including the live
  `Mythic+`/`Ancestral` collision, and restored concise expected/observed result
  logs for every reached Home-preflight requirement.
- Hardened the Equip-to-role-prompt transition with a second OCR layout and one
  bounded retry that remains authorized only while the same verified
  Ancestral detail still offers `EQUIP`.
- Live Farm T19 validation corrected the remaining generator and core
  assignments, accepted their level transfers, matched all eight configured
  modules, completed every Home and session check without a waiver, and
  resumed normal battle handlers.
- Repository-wide validation passed 709 sandbox-compatible tests plus the
  separately permitted localhost HTTP test, for 710 total.
- Implemented in commits `1629bb3` and `31e0191`.

### 2026-07-25 Range-bound Orb Distance presets

- Added named Orb Distance presets and enforced Tier 18 Farm at Attack Range
  `30.00m` with Extra `30.00m` / Workshop `39.00m`, and Tournament at Attack
  Range `98.38m` with Extra `87.16m` / Workshop `80.37m`.
- The battle-only controller requires authoritative Range and panel OCR,
  freshly matches every single arrow tap, verifies strict progress, and blocks
  strategy completion until both values match exactly and the panel closes
  back to the running side menu.
- Retained fixtures validate both Range values, the Distance Adjuster values,
  and every new tap target. Repository-wide validation passed 703
  sandbox-compatible tests plus the separately permitted localhost HTTP test,
  for 704 total. No live battle validation was performed.
- Implemented in commit `5448e82`.

### 2026-07-25 confirmed Tournament launch

- Added a durable, one-shot launch decision to a successful Tournament
  validation receipt. **Start Tournament** performs lightweight freshness and
  ownership checks without rerunning validation, claims the launch before
  input, and uses only verified Home New Battle, Tournament Open, and
  Tournament Battle controls.
- Added automatic and persistent browser/native prompts with **Start
  Tournament**, **Cancel launch**, and **Decide later**. The prompt reminds the
  operator to set Target Priorities for the current Tournament Battle
  Conditions when the battle begins; that setting remains manual.
- Pause, restart, owner mismatch, request supersession, timeout, wrong battle,
  and ambiguous navigation fail closed. Manual launch remains supported, a real
  Tournament never gains Surrender authority, and its normal EHLS/EALS
  initialization remains active.
- Repository-wide validation passed 684 sandbox-compatible tests plus the
  separately permitted localhost HTTP test, for 685 total. Browser JavaScript
  syntax validation and standalone Windows-client publishing also passed. No
  live process or device interaction was used.
- Implemented in commit `0aea936`.

### 2026-07-25 Damage Slider operator log formatting

- Kept the internal Damage Slider target at `1E2` while formatting
  operator-facing target, comparison, and completion messages as `100%`.
- Focused validation passed 120 tests. No live process or device interaction
  was used.
- Implemented in commit `f4ae2b0`.

### 2026-07-25 one-shot Tournament validation

- Made each explicit Tournament selection or managed Start authorize one
  durable, fingerprint-bound validation request. After complete unwaived Home
  preflight, the same runtime atomically owns and starts one verified ordinary
  New Battle, enforces Damage Slider `100%`, validates Ultimate Weapons and
  Spotlight Missiles, and returns only that battle to Home.
- Ownership is checked before every terminal action. Restart, ADB-target
  change, Resume, Tournament identity, stale evidence, or another ambiguous
  boundary fails closed without inherited Surrender authority. Browser and
  native clients show pending, running, cleanup, ready, and failed results.
- The disposable validation battle does not toggle Auto Perks or seed upgrade
  completion. The manually started Tournament still runs normal EHLS/EALS
  initialization before settling into observer behavior.
- Focused validation passed 212 tests. Repository-wide validation passed 665
  sandbox-compatible tests plus the separately permitted localhost HTTP test,
  for 666 total. The standalone Windows client published successfully, and no
  live process or device interaction was used.
- Implemented in commit `edc53ea`.

### 2026-07-25 action-intent log headers

- Added a reusable operator-facing `ACTION` header that states what a guarded
  or multi-step workflow is beginning and why before its tap and swipe details.
- Adopted the header for level-skip initialization, Target Priority, Damage
  Slider, session preflight and Home repair, Daily Gem and mission rewards, and
  Game Over handling.
- Focused validation passed 139 tests. Repository-wide validation passed 649
  sandbox-compatible tests plus the separately permitted localhost HTTP test,
  for 650 total. No live process or device interaction was used.
- Implemented in commit `8bbd3eb`.

### 2026-07-25 Tournament Stun and Damage Slider preflight

- Added Poison Swamp Stun `on` to the Tournament Home contract. The guarded
  detail-panel correction now supports either required state while Farm remains
  Stun `off`.
- Added the battle-only Tournament Damage Slider requirement at `100%`.
  Session validation enforces it before scanning Ultimate Weapons, and Home
  evidence records the control as deferred rather than claiming it was checked.
- The remaining Tournament configuration checks that truly require a battle
  are Damage Slider plus the nine Ultimate Weapon primary toggles and Spotlight
  missiles. Game speed is maintained separately by its runtime handler.
- Regression and full repository validation passed 648 tests, including the
  separately permitted localhost HTTP test.
- Implemented in commit `534a221`.

### 2026-07-25 Tournament Guardian tap authority

- Added retained-fixture-backed Attack and Ally inventory targets so
  Tournament Home setup can replace Farm Guardian chips without falling back
  to forbidden coordinate-only taps.
- Regression coverage requires visible-target selection and validates both
  unequipped inventory cards against the retained Farm loadout. The focused
  Guardian, tap-safety, and clickmap suites initially passed all 58 tests.
- Follow-up `1e0c860` lets the same reconciler safely resume when a prior
  fail-closed replacement left Attack, Ally, Fetch, Summon, or Scout empty.
  Interrupted Attack and Ally cases raise the focused total to 59 passing
  tests.
- Implemented in commits `2bfb653` and `1e0c860`; live reload and gate retry
  remain pending.

### 2026-07-23 offscreen weekly mission chest

- Added a bounded horizontal weekly-chest traversal to Daily Missions. It
  normalizes the retained track position, searches with overlapping guarded
  swipes, and claims only from the fresh frame that exposes the available
  chest.
- Regression coverage verifies the swipe geometry, offscreen search, fresh
  claim authority, initially visible rewards, and Sunday hold/capacity policy.
  The focused suites passed 40 tests; the full suite passed 637 sandbox tests
  plus its separately permitted localhost-socket test.
- Live validation claimed the preserved offscreen chest, dismissed its reward
  reveal, converged at the far edge with `daily=1`, and then completed a Tier 18
  Farm startup and session preflight without waivers or failed checks.
- Implemented in commit `4554f7c`.

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

### 2026-07-16 Ancestral module icon index

- Exhaustively reconciled the owned Ancestral inventory into 24 distinct
  icon/name pairs, six for each module family, including unequipped modules.
- Added a read-only JSON-backed equipped-module index with separate
  Primary/Assist geometry, Ancestral-green gating, confidence and runner-up
  separation, and non-authoritative unknown/ambiguous outcomes.
- Retained fixture evidence for the confirmed GC overview and Project Funding
  at both equipped scales. `test/test_module_icon_index.py` verifies all eight
  equipped identities, catalog completeness, scale normalization, and
  rejection of ambiguous, unreadable, and non-green evidence.

### 2026-07-16 GC module gate and guarded repair

- Added the exact eight-slot GC module mapping to both generated GC profiles
  and included Modules overview evidence in the read-only session preflight.
  Unknown, ambiguous, and non-Ancestral results block without authorizing
  Surrender or equipment changes; only confidently named wrong modules request
  the Home-only repair path.
- Added an app-owned stop → Game Over → Home setup → restart → fresh-preflight
  lifecycle. Module correction acts only at verified `NEW_BATTLE` Home, ranks
  the complete Ancestral inventory from normalized icon data, confirms the
  exact detail name plus Equip/Unequip action, and revalidates the complete
  overview after every transition. At this point the implementation
  deliberately declined level transfer; the 2026-07-23 correction below
  supersedes that behavior.
- Added completeness guards for module captures, complete-modal guards for
  detail OCR, and bounded rewind-to-top behavior for retained Module inventory
  and Event Bots scroll positions. Regression coverage exercises correct,
  wrong, swapped, uncertain, incomplete, transition, and retained-scroll cases.
- Live-validated the full lifecycle with an explicitly developer-owned Tier 18
  run: Project Funding was detected in the Black Hole Digestor slot while every
  other preflight requirement passed; automation Surrendered once, restored
  Black Hole Digestor at Home, restarted, completed EHLS/EALS at waves 20/30,
  and produced a fully valid fresh preflight with all eight expected modules.
  The post-validation developer-owned run was then Surrendered for cleanup and
  the device was left at `NEW_BATTLE` Home under persisted `PAUSED/HOME`.

### 2026-07-17 Poison Swamp Stun preflight correction

- Added profile-owned `Poison Swamp: stun: off` requirements to both GC
  profiles and restricted the compact strategy schema to that supported state.
- Added separate retained templates for the Poison Swamp detail title and the
  checked/empty Stun control. The guarded helper reacquires a complete UW frame,
  derives the detail action from the uniquely detected Poison Swamp tile,
  changes only verified `on` to `off`, reverifies the result, and returns to the
  UW menu. Unknown or incomplete evidence fails closed.
- Fixture and navigation regression tests cover on → off correction,
  already-off behavior, template separation, profile propagation, and the
  preflight evidence merge without ending or leaving the active battle.
- Live-calibrated both checkbox states on a preserved active run, restored Stun
  to off, and exercised the production already-off path at confidence `1.0`.
  The helper dismissed the detail and returned to `RUNNING/UW_MENU`; the
  existing battle was never Surrendered and automation was resumed afterward.

### 2026-07-17 Farm profile and loadout architecture

- Replaced GC as the public recurring-run profile with `farm`, `farm_t18`, and
  `farm_t19_experiment`; the former GC names remain compatibility aliases and
  the command-line default is now `farm`. Glass Cannon is retained only as a
  gameplay concept that can span Farm, Tournament, Milestone, and Dissonance
  purposes.
- Added one non-overridable Farm baseline for Cards, Workshop, Bots, Guardian,
  Auto Pick Perks, and Ultimate Weapon controls.
- Restricted per-Tier and experimental loadouts to Modules, Damage Slider, and
  Target Priority. Compact profiles must explicitly choose `enforce`,
  `observe`, or `preserve`; module and Target Priority presets resolve into the
  generated plan at build time. Damage Slider was initially preserve-only
  pending its guarded setter.
- Made module observation non-blocking, module preservation skip navigation,
  and Target Priority observation read-only. Generated plans carry the resolved
  configuration, and schema-version-2 battle records persist that snapshot.
- Added focused builder, alias, policy, preflight, action, strategy-isolation,
  and battle-record regression coverage. This architectural slice was validated
  offline and did not pause or interact with the active battle.

### 2026-07-17 Tier 18 Damage Slider initialization

- Extended the Farm loadout policy so Damage Slider `observe` and `enforce`
  modes resolve explicit percentages without strategy-name conditionals. Tier
  18 now enforces `1E-22%` during every new-run initialization; Tier 19 remains
  `preserve` for experimentation. The rule waits for the time-sensitive
  EHLS/EALS initialization before opening the Damage panel.
- Added guarded Attack-menu navigation and feedback control. The setter opens
  the freshly matched Damage detail, reacquires authoritative panel and OCR
  evidence before each explicit arrow tap, requires strict progress toward the
  requested value, verifies the final value, and restores
  `RUNNING/ATTACK_MENU`. Ambiguous, unchanged, or regressive feedback fails
  closed; wrong-sized or majority-black direct ADB frames cannot authorize the
  panel evidence.
- Reset each run's gate and structured observation from the strategy's declared
  defaults, and added fixture-backed normalization, navigation, adjustment,
  policy, generated-plan, executor, and reset regression coverage.
- Live validation on an explicitly developer-owned Tier 18 run observed the
  starting value at `100%`, enforced `1E-22%` in 24 strictly verified steps,
  independently re-observed the final value, and returned to
  `RUNNING/ATTACK_MENU` without Surrender.

### 2026-07-17 Farm Cards preset migration

- Fresh inspection found the in-game Cards preset already named `Farm` and
  selected at a verified no-battle Home boundary. Replaced the stale `GC`
  baseline value, clickmap identity, state evidence, and no-battle correction
  target with `Farm`.
- Retained complete live active and inactive Farm frames plus a dedicated Farm
  slot template. Regression coverage requires the Farm identity and separately
  measures its green selected border; the former GC frame is retained as a
  negative so old text cannot satisfy the new invariant.
- Temporarily selected Tournament only to capture the inactive Farm border,
  then verified Farm restored by pixel evidence and returned to `NEW_BATTLE`
  Home. Automation remained in the operator's pre-existing paused state.

### 2026-07-18 Tournament configuration validator

- Added a compact Tournament contract and read-only live validator for Cards
  `Tournament`, Workshop `Tourney`, Bots `Amplify`, Guardian
  `Attack`/`Ally`/`Scout`, all nine Ultimate Weapons, Spotlight missiles, and
  the eight-slot Tournament/Milestone module loadout. Perks are explicitly
  excluded because Tournament battles do not have Perks.
- Reused the shared profile-driven session evaluator and guarded navigation
  route. The CLI requires persisted `PAUSED` control and fresh
  `RUNNING/TOURNAMENT` evidence, never selects or equips anything, uses the
  active battle for Cards, Ultimate Weapons, Modules, Bots, and Guardians, and
  uses the verified Exit Battle → Go Home route only for Workshop. It resumes
  only from authoritative Tournament evidence.
- Added separate Tournament/Tourney/Amplify slot identities plus green-border
  selection checks, Attack/Ally equipped states, a fresh name-reconciled
  Tournament module overview, and positive/negative fixture coverage. Farm
  fixtures prove that visible inactive Tournament labels do not satisfy the
  Tournament contract.
- The initial route exposed the current `Tournament Heat` title as a missing
  `BATTLE_HEAT` variant. Added its dedicated state template and visible close
  control, and made guarded cleanup recognize and close that dialog.
- The optimized in-battle route then exposed a static
  `navigation.menu_guild` coordinate as the Tournament Heat control. A later
  Trophy-layout recurrence proved that no single coordinate is authoritative:
  all in-battle side-menu destinations plus Event/Guild tabs now require
  visible template matches and tap their observed bounding boxes.
- Added a generated passive Tournament strategy. It attempts validation once,
  records conclusive mismatch evidence without requesting repair, permits only
  ad gems and terminal-result handling, persists terminal `WAIT`, and
  suppresses coin-display, recovery, Home, and mission actions. Floating-gem
  collection remains the normal bounded sweep started by an ad-gem collection;
  it is not a continuous Tournament handler.
- Live validation passed every configured requirement on the active Tier 17+
  Tournament. Cards, Ultimate Weapons, Modules, Bots, and Guardians remained
  in-battle; only Workshop used the resumable Home route. The observer returned
  to the same battle without Surrender or configuration changes, collected an
  ad gem, and completed a later status interval with no non-gem action.
- Added the distinct `TOURNAMENT_RESULTS` state and a non-dismissing result
  handler. The live natural result was recorded as a valid 144-row exact
  Round Stats report with summary/detailed wave agreement, then restored to
  Tournament Stats and left in `WAIT`; `OK` was never tapped. Tournament tier
  values such as `17+` are now structured minimum integers. Recent matching
  valid records suppress duplicate capture after restart.
- Regression coverage is in `test/test_tournament_results.py`,
  `test/test_tournament_observer.py`, `test/test_mission_reward_handler.py`,
  `test/test_gc_preflight_navigation.py`, and `test/test_battle_stats.py`.
  The implementation fix is `592acad`; the focused suite passed 73 tests, the
  full suite passed 325 tests, and clickmap integrity reported no errors.

### 2026-07-18 Farm preflight evidence and degraded-handler recovery

- Changed cross-scroll Ultimate Weapon aggregation to preserve nested toggle
  evidence, including the Poison Swamp Stun state that exists only on its
  detail panel.
- Split active/repairable preflight exclusivity from a terminal blocked result.
  A conclusive non-repairable failure still blocks strategy and mission work,
  but bounded ad-gem handling remains available; terminal Game Over behavior
  continues through the battle lifecycle.
- Regression coverage reproduces the live multi-scroll overwrite and verifies
  the degraded handler boundary. The focused suites passed 107 tests and the
  full suite passed 328 tests.
- Live validation on the preserved Tier 18 battle completed every Farm
  preflight requirement with `Poison Swamp: primary=on, stun=off`, released
  normal handlers, and collected the ad gem stranded by the old gate. The
  implementation fix is `453c484`.

### 2026-07-19 no-battle coverage and generated Farm validation

- Implementation commit: `6ed3b6f`.
- Completed the safe no-battle Home/submenu traversal from authoritative
  `HOME_SCREEN` plus `NEW_BATTLE` evidence. The existing Workshop, Cards,
  Modules, Lab, Store, Daily Missions, Event, Guild, Tournament, Settings,
  Ranking, Themes, Inbox, Vault, and Battle History states continued to
  classify without changing claims, purchases, presets, loadouts, or settings.
- Added explicit fixture-backed coverage for the formerly `UNKNOWN` Home Perks
  configuration and Milestones screens. The uppercase Perks configuration now
  shares the `PERKS` primary state, while Milestones has a dedicated
  `MILESTONES` state. The observed Currencies popup and Android Exit Game
  confirmation also have explicit overlay states over their existing primary
  screens. Both primary rules were re-exercised successfully against the live
  screens after the change; the Exit action was never selected.
- Live-validated the generated `farm` plan in one fresh process and an
  explicitly agent-owned Tier 18 battle: Home-only Farm setup passed, EHLS and
  EALS completed at waves 20/30, Damage Slider verified `1E-22%`, Target
  Priority matched the complete resolved order, every session-preflight
  requirement passed, and normal handlers resumed.
- The authorized guarded cleanup Surrender reached Game Over, persisted the
  resolved Farm configuration in
  `logs/battles/Battle20260719T101126-0700.json`, and returned Home without
  starting another battle. The intentionally short report lacked `killed_by`
  and `health_regenerated`; the recoverable capture path retained OCR/source
  evidence, saved the incomplete record, and continued to Home instead of
  forcing global `WAIT`.
- Regression coverage is in `test/test_ui_state_coverage.py` and
  `test/test_game_over_handler.py`. The UI-state suite passed 39 tests,
  `test/test_clickmap_access.py` passed 5 tests, state-definition validation
  passed, recursive clickmap/template integrity reported no errors, and the
  full repository suite passed 332 tests.

### 2026-07-19 Home Daily/Event badge handling

- Implementation commit: `6ed3b6f`.
- Added separate fixture-backed Daily Mission and Event badge measurement for
  Home. The in-battle route retains its closed-menu attention probe and open
  side-menu badge regions; Home now dispatches directly through visible Daily
  Missions and Event navigation evidence. Home Guild handling remains excluded
  until positive badge evidence can distinguish an alert from its static red
  icon.
- The first live no-battle Event pass exposed a retained-tab authority defect:
  Event opened on Bots, but the shared `EVENT` parent state allowed that content
  to be scanned and misreported as four incomplete missions. The handler now
  explicitly selects the visible Missions tab and revalidates `EVENT` before
  any Event Mission scroll, claim match, or inventory capture.
- The corrected normal Home runtime selected Missions, claimed both available
  Event rewards with visible claim-button evidence, logged
  `daily=0 event=2 guild=0`, and returned to complete `HOME_SCREEN` plus
  `NEW_BATTLE`; the Event badge was then absent while the deferred Daily badge
  remained. No battle was started or surrendered.
- Sunday ordinary Daily claims remain banked below capacity, but authoritative
  `8/8 Missions` panel OCR now releases exactly two ordinary rewards so new
  missions have room to arrive. Ambiguous or low-confidence capacity evidence
  fails closed; weekly chests and Event rewards remain eligible without
  consuming the two-claim relief budget.
- Regression coverage is in `test/test_mission_reward_handler.py`: its 29 tests
  passed, and the full repository suite passed 341 tests.

### 2026-07-19 Home ad-gem collection

- Implementation commit: `6ed3b6f`.
- Registered the existing five-gem Home artwork as the explicit
  `HOME_AD_GEMS_AVAILABLE` overlay using tracked available and unavailable Home
  fixtures. Home dispatch collects it before Home handling can start or resume
  a battle.
- Added a Home-specific guarded collection path. It stops any prior bounded
  in-battle tapper, requires a fresh visible `buttons.claim_ad_gem:home` match,
  permits no blind fallback, and verifies that the control disappears. The
  in-battle ad-gem handler retains its existing floating-gem tapper behavior.
- The first live attempt failed closed because state detection's padded region
  found the control while the zero-padding action matcher could not. The shared
  Home control region now covers the observed geometry, and the semantic button
  label reuses the proven full-control template.
- Normal-runtime validation matched and tapped the Home claim at `(124,251)`.
  The gem balance increased from 3564 to 3569, the overlay and action match
  disappeared, and the UI remained `HOME_SCREEN` plus `NEW_BATTLE`; no battle
  was started or surrendered.
- Regression coverage is in `test/test_home_ad_gem.py`. The impacted focused
  suites passed 105 tests before live validation, the post-correction focused
  suites passed 13 tests, and the full repository suite passed 345 tests.

### 2026-07-20 720p emulator compatibility

- Implementation commit: `15b2b8e`.
- Added a centralized screen-geometry boundary that accepts native
  `1080x1920` and `720x1280` captures, records geometry per ADB target,
  normalizes frames into canonical vision space, and maps canonical taps and
  swipes back to native device pixels.
- Calibrated affected Upgrade and Game Over evidence without replacing exact
  visible-action requirements. Retained fixture round trips and a live 720p
  terminal capture verified state detection, 24 ordered perks, and all 144
  clipboard Stats rows.
- Focused geometry, capture, state, clickmap, and Game Over validation passed
  72 tests.

### 2026-07-20 structured battle records and classification

- Implementation commit: `78b37d5`.
- Made structured Battle/Tournament records the canonical completed-run
  artifact and classified Farm, Tournament, and Milestone from strategy plus
  terminal evidence. Reports include resolved settings, ordered perks,
  derived rates, and bounded Coins/min progression; previous-wave lookup now
  reads the records rather than routine terminal screenshots.
- Extended the shared case-sensitive Tower-number scale through named
  magnitudes and `aa` onward. Focused record, classification, and Tournament
  validation passed 33 tests.

### 2026-07-20 managed native Windows control surface

- Implementation commit: `dd1b0f7`.
- Added a loopback versioned Linux API, fixed systemd user-service lifecycle,
  authoritative control acknowledgements, process/PID evidence, persisted
  strategy and ADB settings, guarded paused live-target handoff, and a
  next-run startup-gate policy for attaching to an existing battle.
- Added the self-contained WPF client with an owned passwordless OpenSSH
  tunnel, resizable operational layout, active-state highlighting, current and
  completed battle telemetry, report filters, runtime evidence, independent
  activity filtering, and selectable/copyable log rows. The browser client
  remains available as a fallback.
- The full Python suite passed 431 tests, and the Linux publisher produced a
  self-contained `win-x64` executable with Microsoft's WindowsDesktop SDK.

### 2026-07-21 startup gates and operator evidence

- Commit `e14999c` shortened Event Mission traversal to overlapping viewports;
  its focused handler suite passed 31 tests.
- Commit `5c6519a` generalized upgrade scanning to explicit column regions and
  repaired full-height Workshop Free Upgrade lock detection using a retained
  Shockwave fixture; its focused suite passed 13 tests.
- Commit `372cff3` separated concise operator `ACTION`/`STATUS` entries from
  paired diagnostic detail, made queued-tap success reporting authoritative,
  and defaulted browser/native activity to operational levels. Its focused
  validation passed 87 tests, including the separately permitted socket test.
- Commit `4ab91eb` replaced broad Force Continue with requirement-scoped gate
  decisions, a Farm Flame fallback, and optional strategy-aware Configure Run
  dialogs and CLI controls. The full Python suite passed 482 tests, and the
  repository-root Linux publisher produced the self-contained WPF executable.
- Commit `ef41ab9` made verified Home `NEW_BATTLE` setup the sole authority for
  all three Farm Free Upgrade locks, carried its evidence into session and run
  reports, and recorded missing attachment evidence as non-blocking
  `unavailable_deferred` until the next real boundary. Active preflight retains
  every unrelated requirement without invoking the lock scanner. Validation
  passed 484 sandbox tests plus the separately permitted localhost-socket test,
  for 485 total.
- Commit `5cd9efe` added explicit mid-run strategy adoption after fresh active
  battle evidence. It updates normal behavior and completed-run Farm identity
  without a restart, preserves default next-boundary queueing, and defers
  run-initialization, session-preflight, and Home-only gates until the next
  genuine boundary. The full suite passed 492 sandbox tests plus the separately
  permitted localhost-socket test, for 493 total; the repository-root Linux
  publisher also completed successfully.
- Commit `88b603c` replaced command-like strategy buttons and the adoption
  checkbox with a strategy dropdown and explicit queue/adopt actions. The
  client preserves an unsent choice across status refreshes, distinguishes
  selected/current/pending identity, and disables requests that would be
  no-ops. The repository-root Linux publisher completed successfully.
- Commit `2c06a66` made client/server compatibility explicit through Linux API
  revision and capability metadata. The Windows client disables unsupported
  adoption and, only after confirmation, can restart the one fixed Linux
  control-surface unit over its validated SSH destination before verifying the
  capability after reconnection. Focused validation passed 55 sandbox tests
  plus the separately permitted localhost HTTP test, for 56 total; the
  repository-root Linux publisher also completed successfully.
- Commit `ef8df58` generalized that compatibility decision. The Windows client
  now evaluates its expected API version, minimum Linux server revision, and
  required capability set; revision mismatch alone exposes the same generic,
  confirmed recovery path, and reconnection must satisfy the complete contract.
  Focused validation passed 15 sandbox tests plus the separately permitted
  localhost HTTP test, for 16 total; the repository-root Linux publisher also
  completed successfully.

### 2026-07-22 Workshop retained-mode recovery

- Commit `1505ec7` made the no-battle Free Upgrade lock gate recover when
  Workshop opens on its retained Enhance mode. It selects the explicit Upgrade
  control, reacquires Workshop evidence, and then navigates to the required
  Attack or Defense upgrade category.
- A focused simulator regression starts in Enhance and verifies the navigation
  order. The no-battle integration suites passed 53 tests; the full suite
  passed 493 sandbox tests plus the separately permitted localhost-socket test,
  for 494 total.

### 2026-07-22 Home-boundary preflight and runtime responsiveness

- Commit `dacb715` moved every persistent Farm check available from Home
  `NEW_BATTLE` into complete no-battle setup: Cards, Workshop and Free Upgrade
  locks, Bots, Guardians, Modules, and Target Priority. Serialized
  screen-derived evidence now satisfies the corresponding session-preflight
  requirements, so a newly started battle checks only Auto Pick Perks and
  Ultimate Weapons instead of returning Home. Existing-battle attachments
  retain the guarded compatibility route. The focused suite passed 130 tests.
- Commit `152d3be` anchored Guild reward-badge measurement to the matched Guild
  icon. A retained positive frame and same-layout negative prove badge
  detection when an active Tournament Trophy displaces Guild; the reward
  handler suite passed 32 tests.
- Commit `e0b246f` changed Damage Slider enforcement to batch only exact
  power-of-ten exponent gaps, reacquire settled OCR evidence afterward, and
  recompute dropped steps. Unknown sequences retain single-step feedback and
  partial dispatch failures stop after verification. Damage Slider and run
  initialization validation passed 87 tests.
- Repository-wide validation passed 502 sandbox tests plus the separately
  permitted localhost-socket test, for 503 total.

### 2026-07-22 Tournament boundary preflight and attachment advisories

- Commit `ea7e548` added a corrective Tournament setup at verified Home
  `NEW_BATTLE`. It selects Tournament Cards, Tourney Workshop, Amplify Bots,
  Attack/Ally/Scout Guardians, and Tournament Modules, retains their boundary
  evidence for the Ultimate Weapon-only in-battle check, and deliberately
  leaves Tournament entry manual.
- Tournament attachment now runs its declared read-only preflight instead of
  suppressing it with other startup gates. Authoritative mismatches publish a
  non-blocking browser/native decision with pause, retry, and scoped-continuation
  choices while natural terminal capture remains active.
- Completed Tournament identity follows the distinct Tournament Results screen,
  and terminal-observed Tier is retained independently of strategy identity. A
  no-strategy standard Game Over reports its Tier while remaining `unknown`.
- Repository-wide validation passed 514 sandbox tests plus the separately
  permitted localhost HTTP test, for 515 total. Browser JavaScript syntax and
  the repository-root Linux WPF publisher also completed successfully. No live
  device interaction was used for this code-only change.

### 2026-07-22 Guarded active-battle automation reload

- Commit `3216fb9` added **Reload automation for current battle** to the native
  and browser control surfaces. The fixed automation unit is replaced without
  persisting ordinary `STOPPED`: the existing runtime first acknowledges Pause
  and publishes a fresh `RUNNING` observation, then the replacement must prove
  a distinct MainPID, matching held ADB lock, one-launch `next_run` policy,
  Pause consumption, and its first status before prior control intent returns.
- The configured cold-start policy is restored immediately after the attached
  launch environment is copied. Launch or verification failure remains paused;
  initial owner/precondition rejection changes nothing. Repeated same-state
  directives now have unique identities, are acknowledged by the runtime, and
  force a fresh next-frame status sample without authorizing actions.
- Repository-wide validation passed 523 sandbox tests plus the separately
  permitted localhost HTTP test, for 524 total. Browser JavaScript syntax and
  the repository-root Linux WPF publisher also completed successfully.
- Live validation on 2026-07-22 reloaded the active No Strategy Attack
  Dissonance battle at wave 3314. The original PID acknowledged Pause and
  exited cleanly; a distinct MainPID acquired the refreshed ADB lock, attached
  once with `next_run`, acknowledged Pause, and restored `RUNNING` at wave 3315
  while the configured policy returned to `immediate`. Gate re-arming under a
  strategy that actually declares gates remains in the runtime backlog.

### 2026-07-22 No Strategy observed-run inventory

- Commit `28faa29` made No Strategy a two-phase observation profile without
  adding configured intent or strategy action authority. It passively records
  actual selected presets, Guardian chips, Modules, Target Priority, Damage
  Slider, Auto Pick state, and Ultimate Weapon toggles when their screens are
  visible. Missing fields remain explicit rather than inheriting Farm or
  Tournament values.
- A localized purple sword badge beside Tier records Attack Dissonance identity
  and supports high-confidence `dissonance` classification at standard Game
  Over. Schema-version-3 battle records keep this and every other actual value
  under `observed_run_configuration`, separate from `run_configuration`, with
  field source, phase, confidence, and timestamp.
- Natural No Strategy Game Over now forces full structured capture and Home.
  Verified `NEW_BATTLE` owns a read-only inspection of the three supported Free
  Upgrade locks, then holds Cards until the operator opens Perks configuration.
  First Perk, Ban Perks, and Auto Pick tabs are guarded, fully scrolled, OCRed
  in selected-row order, and backed by retained page images; uncertain results
  stay raw instead of becoming invented settings. The same battle JSON and
  Markdown are updated before normal Home/start handling is released.
- Repository-wide validation passed 543 sandbox tests plus the separately
  permitted localhost HTTP test, for 544 total. The current ignored frame read
  1,071 badge-purple pixels against a 500-pixel threshold; eight retained
  `RUNNING` fixtures were negative. No device input or live terminal-boundary
  validation was used, so the complete natural Game Over path remains in the
  runtime backlog.

### 2026-07-22 automatic No Strategy configuration traversal

- Commit `9285979` replaced operator-presented configuration screens with an
  automation-owned, read-only in-battle pass. It verifies source and
  destination states while
  visiting Cards, in-battle Perks, every bounded Ultimate Weapon viewport,
  Modules, Event Bots, Guild Guardians, and Target Priority, then restores the
  battle. Damage Slider is read when Attack is accessible and is explicitly
  unavailable on Attack Dissonance rather than probing its disabled menu.
- Post-run capture now records the Workshop preset with the read-only Free
  Upgrade lock pass, opens Cards, expands the Home menu, independently verifies
  the retained Perks item region, and opens/captures Perks configuration without
  operator input. The verified `NEW_BATTLE` boundary remains held until all
  three tabs are captured and the same record is updated.
- Both phases synchronize Pause before every input. A mid-pass Pause sends no
  cleanup action; Resume restores a known read-only screen or verified Home and
  retries the current stage. Focused traversal, pause, terminal-state, visual-
  guard, app-stage, observer, record-rendering, and clickmap tests cover the new
  authority boundaries.
- The first live pass exposed two fail-closed navigation/state defects.
  Commit `4565ab4` avoids tapping an already-selected battle menu, and commit
  `d26f633` makes underlying `RUNNING` evidence yield to a specific modal such
  as Perks. Repository-wide validation passed 555 sandbox-compatible tests;
  the single localhost HTTP test passed separately with socket permission, for
  556 total.
- Live validation on the active Tier 18 Attack Dissonance run attached PID
  `3899024` with startup gates deferred, recovered from the retained Perks
  screen, and completed Cards, Perks, bounded Ultimate Weapon scrolling,
  Modules, Event Bots, Guild Guardians, and Target Priority at 17:26:49. It
  returned to `RUNNING` at wave 4120 with the target lock held and every current
  control acknowledged. No configuration control, Home, Exit Battle, or
  Surrender action was used. The future cold-start policy was restored to
  `immediate`; natural Game Over/post-run validation remains pending.

### 2026-07-22 selected-strategy Home setup and Tier 19 start

- Commit `9ebfabc` made the selected native-client strategy part of the
  managed process-start transaction. The control and managed environment now
  contain that strategy before systemd launch, with revision/capability
  compatibility preventing an older server from accepting the dependent
  action.
- Live Home setup exposed and resolved four fail-closed transition/evidence
  gaps: `9f030a8` waits for Guardian inventory after an emptied slot,
  `c942b8a` fills an exact missing Scout slot, `f6a6def` tracks animated
  equipped-module icons within a bounded neighborhood, and `c8b90da` waits for
  a tapped module detail before OCR. All eight Farm modules were corrected and
  authoritatively revalidated before Battle.
- Commit `32cfdbc` added the missing in-run Auto Pick Perks correction. It acts
  only on verified disabled evidence in the Perks panel and requires fresh
  enabled evidence. Live evidence rose from zero to 1,850 green pixels; the
  complete retried preflight passed with no failed checks and released normal
  Farm strategy actions.
- Repository-wide validation passed 590 sandbox-compatible tests plus the
  separately permitted localhost HTTP test, for 591 total. The earlier
  selected-strategy change also published the self-contained Windows client
  successfully on Linux.
- Final live validation atomically started `farm_t19_experiment`, completed
  Cards, Workshop, Free Upgrade locks, Bots, Fetch/Summon/Scout, and the exact
  Modules loadout before the 22:01:03 Battle tap. The resulting Tier 19 run
  completed EHLS/EALS initialization, corrected Poison Swamp Stun and Auto Pick
  Perks, completed session preflight at 22:10:48, and remained under normal
  `RUNNING` automation.

### 2026-07-23 verified tap authority and Target Priority boundary

- Commit `d410b61` removed the nonexistent Home Target Priority route. Complete
  Home setup now retains explicit `battle_only_control` evidence, which cannot
  satisfy the gate; the generated `RUNNING` action remains the sole owner of
  Target Priority observation or enforcement.
- `safe_tap` now fails closed for every coordinate or matchless named runtime
  target unless the caller supplies complete current-frame evidence, a bounded
  target region, and a target-specific verifier. Template-backed names always
  rematch before dispatch and cannot fall back to configured coordinates.
- Added retained-evidence templates for Home navigation, in-battle Target
  Priority, Perks, Workshop modes, Exit Battle, Damage Slider arrows, and the
  missing Scout inventory control. Dynamic upgrade, module, Perks, Ultimate
  Weapon, buy-quantity, and dialog actions now reidentify their exact target or
  authoritative containing control before tapping.
- Commit `d410b61` initially stopped level-skip taps during capture and limited
  one stream frame to one purchase. The urgency-specific entry below records
  the operator-directed replacement of that short-lived constraint. The
  bounded moving-gem sweep remains the only allowlisted blind runtime tapper;
  unchecked gesture taps are isolated to explicit operator tooling.
- Clickmap and state-definition validation passed. Repository-wide validation
  passed 614 sandbox-compatible tests plus the separately permitted localhost
  HTTP test, for 615 total. No live process or device interaction was used.

### 2026-07-23 urgent initial-frame purchase authority

- Commit `5b9f0a2` added an explicit reusable mode to `TapVerification`. It
  evaluates one complete, target-specific initial frame and caches that verdict
  for a caller-owned bounded sequence; static audit coverage limits the mode to
  `core/level_skip_initializer.py` and `core/damage_adjuster.py`.
- EHLS/EALS again continues purchase taps while a raw screenshot is in flight
  and reuses an unchanged live-stream frame until a newer result frame arrives.
  The first frame must still verify `RUNNING/UTILITY_MENU`, the exact target
  box, and a non-Max state.
- Damage Slider now carries the authoritative panel frame with its OCR reading,
  matches the required direction arrow once, and reuses that matched point for
  the exact bounded batch. Settled OCR and strict-progress checks still run
  after each batch.
- Clickmap and state-definition validation passed. Repository-wide validation
  passed 616 sandbox-compatible tests plus the separately permitted localhost
  HTTP test, for 617 total. No live process or device interaction was used.

### 2026-07-23 battle speed and Home-owned Poison Stun

- Commit `6d5f331` added a global battle-only game-speed guard. It verifies the
  localized value and plus control, discovers the current maximum by observed
  progress, and periodically restores a slowdown. Farm explicitly withholds
  its action authority until both EHLS and EALS are complete.
- Commit `b19dfce` moved Poison Swamp Stun to verified no-battle Home Workshop
  setup. The live source locator uses the Ultimate Upgrades heading and exact
  Poison Swamp title to derive an isolated icon target, while session preflight
  consumes the fresh Stun proof and still requires the in-battle primary
  toggle. Attachments retain the guarded battle fallback.
- The same checkpoint repaired Perks startup navigation by replacing dynamic
  progress digits with a stable, bounded bar-edge verifier. The exact
  `80 / 191` failure frame is retained as a regression.
- Bounded live inspection ran only while automation was stopped, measured Stun
  `off`, and restored verified Home `NEW_BATTLE` without changing the control
  or starting a battle. Repository-wide validation passed 630
  sandbox-compatible tests; the one localhost HTTP test passed separately with
  socket permission, for 631 total.

### 2026-07-23 game-speed maximum-probe correction

- Commit `1f6385a` replaced the original no-effect ceiling probe with the
  operator-confirmed normal maximum. Authoritative `x5.0` and perk-raised
  `x6.3` readings dispatch no input; a lower value receives bounded verified
  `+` taps only until the reading reaches at least `x5.0`.
- Stable satisfied readings remain checked every 30 seconds but no longer
  repeat the same no-op log entry. Failures, changed readings, and actual
  corrective taps remain visible.
- `test/test_game_speed.py` covers zero-input handling at `x5.0` and `x6.3`,
  bounded restoration from below `x5.0`, no-progress failure, Pause authority,
  battle-only scope, EHLS/EALS priority, and stable-log suppression.
  Repository-wide validation passed 641 sandbox-compatible tests plus the
  separately permitted localhost HTTP test, for 642 total.
- A guarded active-battle reload attached the replacement without replaying
  startup/session gates and restored the prior control intent. The replacement
  reported `taps=0` and `target_satisfied` at `x6.3`; the following complete
  guard interval produced no speed input or repeated no-op log.

### 2026-07-23 module level-transfer preservation

- Commit `2a2d00b` replaced the shared module repair's hard-coded decline action
  with a verified acceptance of every presented level-transfer dialog. The same
  path owns both Primary and Assist replacements, and correction now stops if
  the acceptance cannot be authorized or the dialog does not dismiss.
- The correction preserves the role-based level allocation during module
  changes. At the reported progression boundary, the operator's expected state
  was level 201 for every Primary and the highest available levels for Assist
  modules (then approximately 193–194); those progression-dependent values are
  operational evidence rather than hard-coded policy.
- Regression coverage exercises the complete Equip → role selection → transfer
  acceptance sequence independently for Primary and Assist, the failed-accept
  path, and removal of the old decline clickmap action. Repository-wide
  validation passed 634 sandbox-compatible tests plus the separately permitted
  localhost HTTP test, for 635 total. No live module replacement was performed
  during validation.

---

## 📘 Documentation

- 2026-07-16: Split the active backlog by domain, separated open anomalies from
  resolved operational history, and extracted current runtime architecture from
  the dated review handoff. Added an on-demand handoff template that excludes
  stale runtime and validation claims plus an on-demand maintenance guide for
  future lifecycle updates. Preserved the complete pre-split backlog and
  architecture narrative under dated history paths.
- Created `core/input_policy.md` to document dual-path tap architecture
- Updated `README_UPLOAD.md` with summary of input tap architecture and assistant behavior
- Updated `PROJECT_SCOPE.md` to reflect dual-path tap architecture, overlay support, and tap handler split
