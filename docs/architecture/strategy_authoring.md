# Strategy Authoring Architecture

This document defines the contract for GUI-authored strategy bases and
strategies. The sparse model, immutable Base revisions, immutable custom
Strategy lineages, restore-as-new workflow, and current Farm editors are
implemented. Profile-local Module, Target Priority, and Orb definitions now
have additive API discovery, managed native editors, and an explicit
catalog-bound **Edit a copy...** transition from shared preset to local intent.
Module presets also have one immutable installation-local custom catalog plus
authoritative native eight-slot previews and save-as-new creation; the expanded
disposable Windows runtime smoke remains. The original Farm profile format
remains supported as a compatibility facade.

The runtime architecture remains authoritative for action ownership and
execution. This authoring layer resolves reusable, operator-friendly source
documents into the same kind of explicit, self-contained generated plan that
the generic runtime already consumes.

## Goals and boundaries

The editor should eventually expose every supported aspect of a strategy, not
only Perk settings or the current Farm loadout fields. It must also allow an
operator to edit reusable bases such as `Farm` without making a base directly
selectable as a runnable strategy.

The authoring model must:

- support sparse bases and sparse strategy-local settings;
- make inheritance, override, observation, and intentional non-management
  visible in the GUI;
- keep a published strategy stable when a base is edited later;
- resolve and validate all inheritance before runtime;
- preserve protected generated rules and action sequences; and
- publish independently from activation.

The first implementation supports zero or one base per strategy. Arbitrary
inheritance chains, mixins, and automatic propagation are deliberately out of
scope. They can be reconsidered only if real authoring needs justify their
additional resolution and review complexity.

## Authoring entities

### Setting definition

A setting definition is registry metadata shared by validation, resolution,
the API, and the GUI. It gives a stable setting ID its display label, section,
editor type, allowed policies, normalizer and validator, dependencies, runtime
destination, and observation or repair capabilities.

The registry describes supported authoring inputs; it does not grant action
authority. Generated-only rules, low-level taps, and executor sequencing are
not editable settings. The existing shared builders continue to derive and
validate those protected details.

### Base

A base is a named, versioned, sparse collection of setting directives. It may
include Cards, Bots, Guardians, Workshop, and Perk Bans while omitting Auto
Pick Order, Target Priority, locks, Poison Swamp stun, or any other setting.
Omission means the base has no opinion about that setting.

A base entry may be `enforce` or `observe`. A base does not need an `ignore`
entry because omission already means it supplies no behavior. A published base
revision is immutable. Editing a base publishes a new revision and leaves
existing strategy publications unchanged.

A base is an authoring component, not a complete strategy. It cannot be
activated or executed directly.

### Strategy

A strategy contains its own metadata, an optional pinned base reference, and a
sparse set of local directives. Its GUI states are:

| Strategy state | Stored meaning |
| --- | --- |
| `Inherit` | No local directive. Use the pinned base entry if one exists; otherwise leave the setting unmanaged. |
| `Override · Enforce` | Use the strategy's local value and require it to match. |
| `Override · Observe` | Use the strategy's local reference value for comparison and reporting without changing or blocking on a confident difference. |
| `Ignore` | Intentionally do not inspect or change the setting, masking any base entry. |

`Inherit` is represented by absence, not by copying a base value into the
strategy. `Ignore` is explicit and remains meaningful even when the current
base omits the setting: it prevents a future rebase from silently beginning to
manage it.

An ignored local directive may retain a dormant value for authoring
convenience. The resolver and generated runtime plan do not consume that value;
it is available only so the operator can restore enforcement or observation
without reconstructing the setting.

### Publication

A strategy publication is the executable boundary. It contains:

- the sparse strategy source;
- the pinned base identity and revision, if any;
- an embedded snapshot of that exact base revision;
- the fully resolved settings and their provenance;
- the generated runtime plan; and
- source, base, resolution, and plan fingerprints.

Runtime loading never follows a mutable base pointer. It consumes the
self-contained generated plan and resolved run configuration from the
publication.

### Immutable custom-Strategy lineage

Every validated custom Strategy publication is retained as one immutable
logical revision while preserving one stable Strategy ID. The fixed
`config/strategies/custom/<id>.profile.yaml` document remains the latest-
publication compatibility facade used by runtime and older clients. Runtime
loads only that self-contained facade; it never reads history or resolves a
mutable Base while running.

The server owns all history names beneath the fixed custom directory:

- `history/<id>.strategy.<logical-version>.yaml` is the append-only revision;
- `transactions/<id>.publication.yaml` is a recoverable publication journal;
  and
- dot-prefixed files in `transactions` are transaction-owned staging objects.

Clients supply an allowlisted Strategy ID and logical version, never a path.
The catalog writer lock, fixed safe-ID rules, no-symlink checks, request and
file-size limits, and directory durability rules apply to the facade, history,
and transaction directories. A revision envelope contains the complete exact
publication: normalized sparse source, pinned Base reference and embedded
snapshot, resolved values/provenance, generated plan, logical version,
publication time, source/Base/resolution/plan/publication fingerprints, and a
server-created audit identity and origin. Bundled Strategies and Base revisions
remain immutable in their existing stores.

History, not the presence of the current facade, is authoritative for the next
logical version. Retirement removes the latest facade from active catalogs but
retains the lineage. Ordinary publication cannot silently reuse that ID;
managed restoration publishes the next version in the same lineage. The legacy
`retired` archive remains unchanged evidence and is adopted only when identity,
version, and fingerprints are unambiguous.

#### Recoverable publication order

Publication is an explicit recoverable transaction because the history object
and latest facade cannot be replaced atomically together. Under the one catalog
writer lock, Linux performs this order:

1. normalize, resolve, build, and fully validate the proposed self-contained
   publication, allocate the next history-derived logical version, and create
   its revision envelope;
2. durably create the journal, immutable revision and latest stages, and—when
   replacing an existing facade—an exact previous-facade backup;
3. hard-link the revision stage to its final immutable history name, remove the
   stage name, and `fsync` the history directory;
4. atomically replace the latest facade with the exact staged publication and
   `fsync` the custom Strategy directory; this directory sync is the durable
   commit point; and
5. remove the stages and journal and `fsync` the transaction directory.

A handled failure before the commit point restores the prior facade, removes
the uncommitted final history link, and clears safe staging artifacts. An
abrupt interruption is reconciled before catalog, history, or publication work:
if the fingerprint-bound final revision exists, recovery verifies it and the
journal, advances the facade to that exact publication when necessary, syncs
both directories, and cleans up; if it does not exist, recovery restores the
previous facade if needed and aborts the staging. Any mismatch, symlink,
unexpected artifact, or external facade change fails closed as a catalog
conflict without overwriting evidence. Reconciliation is deterministic and
idempotent, so reopening or retrying an interrupted identical request cannot
allocate a duplicate or conflicting revision.

#### Conservative adoption

On catalog open, Linux conservatively adopts an existing schema-1 or schema-2
custom latest publication as its lineage's retained revision without rewriting
the facade. The exact source, resolution, embedded Base state (when present),
and generated-plan behavior are validated and preserved; schema-1 values are
not reinterpreted as inheritance. Existing retirement archives are considered
independently and adopted only when their identity and fingerprints agree.
Repeated opens are idempotent. Duplicate versions, fingerprint disagreement,
malformed documents, symlinks, and ambiguous lineage evidence produce catalog
errors and audit entries while a separately usable latest facade remains
available when safe.

#### History review and restore as new

History summaries are newest-first for clients and include stable identity,
display name, logical version, publication time and status, all review
fingerprints, Base pin, family/Tier, server-owned origin/audit identity, rule
count, current validation state, and migration warnings. Expanded generated
plans are never returned. Linux computes semantic comparisons using the same
source and resolution vocabulary as authoring: directives, effective values
and provenance, Base pin/snapshot, local overrides, explicit Ignore entries,
generated-plan fingerprint/rule count, metadata-only changes, and current
validation errors.

Restore is reviewed publication, not file rollback. Preview requires both the
selected immutable revision fingerprint and the source fingerprint of the
latest facade the client opened (explicitly absent for a retired lineage).
Linux loads the exact retained publication, verifies every embedded snapshot
and fingerprint, re-normalizes and rebuilds with current trusted code using the
historical embedded Base rather than current Base lookup, computes the semantic
comparison, and returns a review fingerprint without writing. Confirmation
rechecks all three fingerprints under optimistic concurrency and publishes the
historical intent as the next immutable revision with origin
`restore_as_new`. It never mutates the selected revision, selects or activates
the Strategy, restarts automation, changes Pause, or changes runtime control.
Ordinary authoring, older facade publication, adoption, and restore origins are
assigned only by trusted server paths.

## Policy semantics

The authoring policies are intentionally limited to three:

| Policy | Contract |
| --- | --- |
| `enforce` | Inspect and require the resolved value. Repair only through an explicitly implemented and verified repair contract; otherwise block the applicable strategy gate. |
| `observe` | Inspect when authoritative observation is supported and record the configured reference plus evidence. Do not change the setting or block on a confident difference. |
| `ignore` | Do not inspect or change the setting. A strategy-level ignore masks an inherited directive. |

There is no separate “require but do not repair” policy. That is already the
safe behavior of `enforce` for a setting with no verified repair capability.
Whether a repair exists is registry/runtime capability, not a fourth operator
policy. A later advanced control may narrow permission to use an available
repair, but absence of that control must remain fail-closed.

The base editor therefore offers `Not included`, `Included · Enforce`, and
`Included · Observe`. The strategy editor offers the four source states in the
strategy table above.

## Deterministic resolution

Resolution operates on one pinned base snapshot and one strategy source. For
each registered setting:

| Base entry | Strategy entry | Resolved result |
| --- | --- | --- |
| Present | Absent | Inherit the base value and policy; provenance is the base revision. |
| Absent | Present override | Use the strategy value and policy; provenance is local. |
| Present | Present override | Use the strategy value and policy; provenance is local and the base value remains available for review. |
| Present or absent | `ignore` | Do not generate inspection or correction behavior; provenance records the explicit local mask. |
| Absent | Absent | Unmanaged; no inspection or correction behavior is generated. |

Resolution is generic and independent of strategy names. It returns both the
resolved intent and provenance. The Farm builder then translates supported
resolved settings into the existing compact builder contract and generated
plan. `YamlStrategy` remains generic and never performs inheritance.

Resolution rejects unknown setting IDs, disallowed policies, invalid values,
missing dependencies, incompatible base families, unavailable base revisions,
and ambiguous schema migrations before publication. A setting is emitted to
the generated plan only through its registered adapter and the builder's
existing validation.

Conceptually, sparse authoring source looks like this:

```yaml
kind: strategy
id: farm_t18_custom
base:
  id: farm
  revision: 4
settings:
  auto_pick_order:
    policy: ignore
  target_priority:
    policy: enforce
    value:
      preset: farm_default
```

The stored publication additionally embeds base revision 4, the resolution
result and provenance, and the generated plan. The exact on-disk schema is
versioned and validated rather than inferred from this illustrative fragment.

## Base revisions and reviewed rebasing

Publishing a new base revision does not alter or republish any strategy.
Strategies continue to use their pinned revision and embedded snapshot. The
catalog may report that a newer compatible revision is available.

Rebasing is an explicit editor operation. Before changing the pin, the GUI
shows a semantic diff:

- settings added, removed, or changed by the base;
- inherited resolved values that would change;
- local overrides that remain unchanged;
- explicit ignores that remain ignored; and
- new validation errors or dependency changes.

The same reviewed operation owns the first Base attachment for an existing
editable Strategy that currently has no pin, including conservatively opened
schema-1 profiles. The client may offer compatible server-catalogued Bases but
cannot publish the new reference until Linux has reviewed the complete
`No Base -> pinned Base` resolution. This keeps the Strategy ID and local
directives intact; cloning is not required merely to establish its first Base.

Accepting the rebase updates the strategy draft and requires normal validation
and publication. Canceling it changes nothing. Publication and activation
remain separate operations, so a newly published revision cannot silently
replace the active runtime strategy.

## Runtime gates and action authority

At a Home/new-run boundary, an enforced mismatch blocks the strategy from
advancing past the applicable configuration gate unless a verified repair
owns and confirms the transition.

If an enforced mismatch is discovered in an already running battle, the
runtime must not express that condition as global Pause. `RuntimeActionAuthority`
creates a run-scoped Strategy Action Gate and answers every decision through
four typed classes:

1. **Observation** has no input authority. Capture, detection, OCR, state and
   wave updates, activation tracking, passive evidence, and status publication
   continue under every gate and under global Pause.
2. **Auxiliary collection** covers only explicitly named independently safe
   collectors. An active Strategy Gate may allow the in-battle ad gem and its
   bounded floating-gem scan, Daily Gem Store, Daily and Weekly Mission
   rewards, Event Mission rewards, and Guild chest rewards. Their schedulers,
   badge checks, Sunday hold, claim bounds, eligibility, and cooldowns remain
   authoritative; the gate never makes a reward due.
3. **Strategy action** covers strategy and mission ticks, overlays,
   configuration, Perks navigation, game-speed correction, upgrade-detail
   handling, auto-return, and unknown-state recovery.
4. **Lifecycle action** covers New Battle, Go Home, Surrender, Exit Battle,
   restart, and every other transition that can alter the run boundary.

Normal running retains its established policy. Global Pause is stronger than
the Strategy Gate: observation continues, but every input and handler action,
including auxiliary collection, is blocked. Activity continuity, run
initialization, session preflight, exclusive validation, and other exclusive
screen owners are also stronger and block all auxiliary collectors; the
matching owner alone may execute its already-bounded internal validation or
transition. Initialization is therefore not reclassified as a terminal
Strategy Gate.

The gate is bound to the authoritative run identity when one is available and
survives temporary menus and overlays in that same battle. It changes only for
successful validation, an accepted retry or run-scoped waiver, an explicit
active-battle strategy/policy change, a separately authorized repair
transition, a changed authoritative run identity, or a genuine natural battle
boundary. An `observe`/notification-only mismatch records evidence without
activating it. Natural Game Over releases the gate before ordinary terminal
handling; the gate itself cannot create that boundary.

Multi-screen auxiliary collectors claim an exclusive route from freshly
verified `RUNNING` evidence before their first input. Every swipe or tap
rechecks the control state, route and battle identity, expected screen, and
typed auxiliary authority. Authority loss retains collector-owned cleanup for
a later verified resume, while Game Over, Home boundary, identity change, or an
unexpected screen abandons the route without cleanup input. No auxiliary route
may invoke generic recovery or navigate through New Battle, Go Home, Exit
Battle, Surrender, or restart. The floating-gem scan uses the same per-input
authority check on every tap without capture/OCR work in its timing-critical
loop, and its cadence is anchored to elapsed time so guard latency does not
accumulate.

The gate does not authorize Surrender, Exit Battle, restart, or a manufactured
run boundary, and it neither writes `PAUSED` nor changes `AUTOMATION.state`.
The separately guarded repair workflow still requires its profile/operator
authority. The control surface states the distinction directly: “Strategy
actions blocked — observation and safe collectors remain active.” The full
matrix and structured status contract are documented in
[`runtime.md`](runtime.md#typed-runtime-action-authority).

## GUI contract

The authoring surface uses one editor framework for bases and strategies:

- The catalog groups `Bases` and `Strategies` in the left pane. Bases cannot be
  activated and bundled items can be cloned before editing when immutable.
- Sections organize supported settings, initially including Setup, Perks,
  Loadout, Ultimate Weapons, In-battle behavior, and Review & Publish.
- Every row shows setting name, source state, effective policy, resolved value,
  and provenance. A local override offers “Reset to inherited.”
- `Show active only` keeps ordinary editing compact; `Show all settings`
  exposes the complete registry and makes omitted settings available.
- Value editors are selected from registry metadata. Specialized editors own
  ordered lists, bans, presets, numeric bounds, toggles, and structured values;
  the GUI does not construct raw executor actions.
- The registry supplies each setting's normalized initial value plus complete
  behavior-free editor metadata: managed options and fields, list limits and
  ordering authority, fixed-value constraints, dependency labels, and
  structured-toggle restrictions. Python normalizers and action adapters are
  not serialized. The native client constrains drafts from that contract and
  still asks the backend to validate and resolve them.
- A strategy whose base has a newer revision shows an update banner and opens
  the reviewed rebase diff before changing anything.
- An editable existing strategy with no Base may select its first compatible
  Base and must accept the same backend semantic review before publication;
  this does not clone, rename, or activate the strategy.
- Review & Publish displays source changes, resolved changes, validation
  results, generated-plan identity, and whether the active strategy will remain
  unchanged after publication.

The API returns the same source, policy, resolution, provenance, capability,
and validation vocabulary so the WPF client does not duplicate resolver rules.

### Profile-local loadout definition backend

Sparse authoring source schema 3 gives `modules`, `target_priority`, and
`orb_distance` one exact preset-or-local value contract. A directive value is
either `{"preset": "<id>"}` or `{"local": <definition>}`; it cannot contain
both or any extra field. Shared preset IDs and catalogs remain supported.
Modules local data is the exact validated eight-slot-to-module mapping, Target
Priority local data is the complete ordered target list, and Orb Distance local
data contains exactly `range_basis`, `extra`, and `workshop`. The existing
Module family/slot catalog, Target Priority order validator, and Orb Distance
normalizers remain authoritative.

Every effective schema-3 loadout definition has a server-owned
`definition_snapshot` in resolution:

```text
schema_version: 1
source: preset | local
preset: <id>                    # preset source only
definition: <normalized exact definition>
range_relationships: [...]      # Orb Distance only
fingerprint: <canonical payload fingerprint>
```

Orb `range_relationships` retains the complete normalized relationship set
used by the generated range-selection/preserve action, including the selected
relationship exactly. Semantic effective comparisons include the complete
snapshot, so a preset-data change is reviewable even when the source preset ID
does not change.

A new immutable Base revision stores its sparse schema-3 source plus retained
resolution and resolution fingerprint. A new Strategy publication keeps the
existing outer publication schema 2 but adds `base_resolution` beside the
embedded `base_snapshot`; its final resolution retains every effective
definition snapshot. For a pinned schema-3 Base, the Base fingerprint covers
both source and retained resolution. Immutable Strategy revisions retain the
same evidence. Current validation, comparison, and restore-as-new rebuild from
those snapshots without consulting a later Base revision or shared catalog.
Expanded plans and filesystem paths remain redacted.

The protected Farm adapter passes a retained preset through a validated
`preset_snapshot` builder input, preserving the existing preset-bearing plan
and run-configuration structure without rereading the catalog. A local input
uses the same normalized effective definition and produces equivalent runtime
requirements/actions without falsely claiming shared-preset provenance. The
legacy compact `{mode, preset}` builder path and bundled generated files remain
structurally unchanged; `YamlStrategy` remains generic.

Stored sparse schema-2 publications and Base revisions retain their exact
source, resolution, fingerprints, and compact builder semantics. Readers do
not eagerly rewrite them. Any prospective edit of a schema-1 compact profile
or schema-2 sparse source crosses the explicit schema-3 boundary before it can
publish, preventing a newly edited Strategy from remaining catalog-dependent.

Server revision 24 advertises
`strategy_authoring_local_loadout_editors_v1`, and the native client's minimum
revision advances to 24 in the same change. The three registry entries retain
their revision-23 `preset` editor type, `{preset: ...}` initial value, and
top-level preset field. Each adds one schema-version-1 `local_editor` object
whose key, label, initial value, value kind, fields/options, exact-list
constraints, server-normalized-text flag, and cross-field uniqueness contract
are validated before exposure. Revision-23 clients ignore this additive
object and continue exact preset-only round trips. A schema-3 local selector
has no `preset` field for such a client to select or synthesize, so the older
editor fails closed instead of reinterpreting it.

The managed native editor derives its preset/local form selector from those
server keys and labels. Module rows are the exact server catalog slots with
family-filtered choices and cross-row uniqueness; Target Priority is the exact
server membership with operator-controlled ordering; Orb Distance is the
three server-declared text fields. The client keeps independent dormant preset
and local drafts while rows move through sparse Base states or Strategy
Inherit/Override/Ignore states, including reconstruction after validation. It
does not normalize Orb values or resolve definitions. Existing authoring
responses may contain schema-3 source values, `definition_snapshot`, Base
`resolution_fingerprint`, and `base_resolution`; clients continue treating
the latter three as server-owned review evidence rather than editable data.

Server revision 31 adds `strategy_authoring_preset_local_copy_v1` and the
`materialize_loadout_preset` authoring operation. Existing resolution
`definition_snapshot` data is the correct Linux materialization primitive, but
it is not a safe WPF copy source: a row can be inherited, ignored, dormant, or
backed by an immutable retained publication snapshot rather than the current
catalog choice. Each of the three preset/local registry entries therefore
includes a `preset_catalog_fingerprint` for the exact catalog snapshot offered
to the client. WPF submits exactly the setting ID, selected preset ID, and that
fingerprint. Linux reloads one current catalog snapshot, rejects a mismatch as
stale, and calls the ordinary definition-snapshot resolver and setting
normalizer against that same snapshot. The response contains the exact
normalized local definition and its server fingerprint, with `published:
false` and `publication_activates_strategy: false`.

**Edit a copy...** is enabled only for an editable Base or Strategy row whose
active source state is Enforce or Observe and whose shared-preset form has an
exact selection. Bundled read-only Strategies keep the control disabled;
cloned and editable custom Strategies use the same row contract. On success,
the client first proves that the response matches the requested setting,
preset, catalog fingerprint, and declared local-editor shape, then atomically
replaces the dormant local value and switches the row to **Profile-local
definition**. It does not save or publish a document, create or select a
preset, select or activate a Strategy, queue a boundary action, or apply
runtime configuration.

A local draft becomes meaningful when the source already contains a local
definition or the operator has selected or changed that local form. If such a
draft is dormant, **Edit a copy...** asks explicitly: replace it with the
normalized selected preset, retain it and switch to it, or cancel with both
forms untouched. The untouched server-supplied default local seed does not
trigger that replacement prompt. Unknown presets, stale fingerprints,
normalization/shape rejection, missing capability, timeout, interruption, and
other response failures leave the selected preset and complete local draft
intact. Base omission/reinclusion and Strategy Inherit/Override/Observe/Ignore,
validation reconstruction, review, history, and publication continue using
the existing dormant-value and source-authority paths.

### Managed custom Module presets

`config/loadouts/modules.yaml` remains the immutable bundled Module catalog.
The server merges it with an operator-owned custom catalog under the fixed,
Git-ignored `config/loadouts/custom/modules` root. That root is constructor- and
CLI-injectable for isolated tests and disposable smoke environments, but no API
request or response accepts or exposes a filesystem path. Each custom ID maps
to one fixed immutable filename. Safe IDs, bounded no-follow reads, a catalog
writer lock, same-directory staged durable creation, collision rejection, and
deterministic stage reconciliation protect the store. A custom ID can never
shadow a bundled ID.

One merged catalog snapshot drives the Module registry options, legacy profile
summaries, rich preset details, prospective Base/Strategy resolution, and
publication. Every item exposes its stable ID, display name, bundled/custom
origin, immutable editability state, complete normalized definition, and the
authoritative ordered eight slots with slot label, family, role, and assigned
Module. The existing Module normalizer remains the sole authority for exact
slot membership, family compatibility, known Modules, and cross-slot
uniqueness. Existing `{preset: id}` values are immutable references: there is
no overwrite, rename, deletion, retirement, or mutable pointer in this slice.

Server revision 25 adds `managed_custom_module_presets_v1` and the authenticated
`create_module_preset` authoring operation while retaining the complete
revision-24 option and nested-local-editor shapes. Creation accepts exactly one
server-resolved source preset or one current profile-local definition, creates
a new ID, returns structured validation/conflict errors, refreshes the merged
catalog, and never publishes, selects, or activates a Base or Strategy. The WPF
client shows the selected preset's eight-slot metadata and read-only bundled or
immutable custom label. **Duplicate preset...** honestly names the immutable
exact-copy operation; **Save as preset...** remains the separate Module-only
operation that submits the current edited local definition. After success,
incremental option reconciliation keeps every retained selection object visible
and explicitly selects the new preset in that row. Neither action is the local
**Edit a copy...** transition. The normal Validate → Review → Publish boundary
remains. Failure leaves the open draft, dormant forms, and selections intact.
Missing capability hides creation controls and the revision-25 compatibility
check fails closed. Target Priority and Orb Distance acquire no managed custom
preset catalogs from local-copy authoring.

Base revisions and Strategy publications continue retaining normalized
definition snapshots. History comparison, restore-as-new validation, and plan
loading therefore remain valid if the custom catalog later becomes unavailable;
they do not acquire a new catalog dependency from custom preset authoring.

### Save-backed setup capture

Server revision 29 added `save_backed_setup_capture_v1`. Server revision 30
retains it and adds `save_backed_setup_capture_v2`, whose terminal receipts
carry the typed action-authority outcome and require a separate explicit retry.
**Capture current setup as…** does not define another loadout format: one
runtime-owned `forced_serialization` bundle is projected through the existing
Farm setting registry, setting normalizers, local loadout selectors, and
Module preset store. Normally Capture requests that serialization itself. If
an active-battle Return Control has already persisted an exact forced-save
receipt and Paused on a trusted Strategy mismatch, Capture may instead consume
that same process-local typed acquisition. The ledger labels this as
`retained_return_control_refresh`; it performs no new device input, does not
resolve Return Control, and cannot be reconstructed from the durable receipt.
Only complete observations authorized by the resolved mapping's explicit
validation or compatibility allowlist enter `settings`. Unmapped, incomplete,
unsupported, or not-yet-authorable values remain explicit `unresolved` rows
with their source check IDs and safe observed value where available. The
preview includes redacted acquisition timing and binding fingerprints plus
process/scope/round workflow binding; cached or passive reads cannot be
relabelled as a capture.
It also fingerprints bounded capture-origin metadata: a newly requested
refresh remains distinguishable from a process-local Return Control reuse after
the mutable workflow ledger moves on. That origin is review provenance only
and cannot replay the acquisition or grant action authority.

If the guarded refresh restores its source, mapping, projection, acquisition,
or incomplete identity failures are reported without changing the operator's
prior action-authority state. A fresh active/resumable battle contradiction
enters a running-battle Strategy Gate: observation and explicitly safe gem
collectors may continue, while Strategy and lifecycle input yield. A proved
Home New contradiction, or an attempted lifecycle transition whose source
restoration cannot be proved, persists Pause because the input owner is no
longer safe. Ready or terminal receipt-write failure retains the exact
process-local result for atomic-write retry without another serialization or
authority change. Reopening a terminal capture is inspect-only; **Try capture
again** is the separate operator intent that may request another refresh.
Capture is intentionally save-only because the supported UI checkers cannot
produce one coherent, same-boundary authoring projection. Its `unavailable`
result does not disable the separate Battle History, configuration-discovery,
No Strategy, or safe-collector UI fallbacks.

Saving a Module loadout calls the ordinary immutable custom Module preset
owner. It is save-as-new: an existing ID is a conflict even if its visible
definition happens to match, because a Module preset does not embed enough
capture provenance to prove a failed-receipt retry.

Saving a custom Strategy first requires a fingerprinted
captured-versus-Base review. A selected Base is comparison context only; it is
not pinned into the captured source and cannot make an unresolved save field
look inherited or captured. Linux builds a normal sparse schema-3 Strategy
source, validates and resolves it through `StrategyProfileStore`, and stores
one immutable draft beneath that owner's fixed `captured_drafts` directory.
Only an existing draft whose embedded capture, source, difference, unresolved
rows, and review fingerprints all match may recover an atomic-create receipt
failure. The captured-draft catalog reports malformed entries rather than
silently adopting them.

The capture-specific difference view compares normalized effective definitions,
not whether an equivalent value was represented by a shared preset or a local
definition. Guardian-chip and Perk-ban order is canonicalized because those
runtime comparisons are set-based; representation/source changes remain visible
as provenance-only differences. Ordered settings such as Target Priority and
Auto Pick priority retain order semantics.

Captured drafts remain unpublished, unselected, unqueued, unapplied, and
inactive. The native Strategy authoring catalog can reopen their source later;
the selected draft's own stored capture origin, captured-versus-Base review,
and unresolved rows are displayed with it. The CLI and detail API expose the
same durable draft. CLI Strategy saving is deliberately two-step:
`review-strategy` prints the exact review fingerprint and a later
`save-strategy --review-fingerprint <sha256>` must supply it. Publishing still requires
the ordinary Linux Validate → Review → Publish flow. Saving a capture never
performs that publication or changes process, battle, Strategy, terminal, or
input authority.

## Code ownership

Implementation should introduce these responsibilities without moving them
into the runtime evaluator:

- A setting registry owns metadata, normalization, validation, dependencies,
  and adapters for all authorable settings.
- A generic resolver combines a sparse base snapshot and sparse strategy source
  into resolved values plus provenance.
- A versioned base store owns immutable base revisions, catalog reads, stale
  write protection, and atomic publication.
- A Module preset store owns the single merged bundled/custom catalog and its
  immutable save-as-new lifecycle. It exposes definitions and path-free preview
  metadata through the same snapshot used by resolution.
- `StrategyProfileStore` (or a narrowly separated authoring store behind it)
  owns strategy drafts and publications, embeds the pinned base snapshot, and
  retains the existing fixed-directory and fixed-filename safety boundary. It
  also owns immutable save-backed captured drafts and their exact review
  fingerprints; they reuse ordinary Strategy source rather than a second
  schema. It also owns immutable lineage/adoption, recoverable publication
  transactions,
  semantic history comparison, restore-as-new, and custom-Strategy retirement
  under the same catalog writer lock; the client never supplies a history,
  transaction, or archive path.
- The Farm authoring adapter feeds resolved settings into the existing shared
  strategy builder. Generated plans remain validated output, not user-authored
  input.
- The control-surface API is additive. Existing endpoints remain a compatibility
  facade while a capability/schema revision lets the new WPF client select the
  richer authoring model.

Atomic publication, optimistic fingerprint checks, immutable bundled sources,
safe IDs, file-size limits, no symlink following, and separate activation are
existing security and concurrency properties that the new stores must retain.

## Migration and delivery

Existing schema-version-1 custom profiles are migrated conservatively: their
current complete values become explicit local directives so behavior is
preserved. Migration must not guess that a matching value was intended to be
inherited. Bundled Farm and Tournament sources may be converted from known
ownership because their source and generated plans are repository-controlled.

Before the new publication format becomes active, regression fixtures must
prove that resolving the migrated bundled and legacy examples produces the
same protected generated plans and run configuration, except for intentional
schema/provenance metadata.

Delivery is split into independently reviewable slices:

1. Backend authoring model: registry, base store, sparse schemas, resolver,
   embedded pinned snapshots, provenance, conservative legacy conversion, and
   generated-plan regression tests. This slice does not change runtime
   behavior.
2. Additive API and editor shell: base/strategy catalogs, row state controls,
   validation, rebase review, and publish review while preserving the current
   client facade.
3. Complete value editors: add specialized editors for every registered
   setting, then perform Windows GUI smoke validation. The implementation and
   Linux cross-validation are complete. The operator completed the available
   Windows runtime smoke checks on 2026-08-02 with no blocking issue reported;
   that report is useful runtime evidence but is not exhaustive Windows
   validation.
4. Runtime Strategy Action Gate: implement and validate the typed observation,
   auxiliary-collection, strategy-action, and lifecycle-action matrix, guarded
   collector routes, structured status, and distinct native presentation before
   relying on running-battle enforcement from newly editable settings.

Each slice should be completed and validated in its own development thread.
The actionable sequence is tracked in the
[`runtime and validation backlog`](../backlog/runtime-and-validation.md).

All four original slices, the immutable-history/safe-fallback slice, managed
custom Module preset creation, save-backed setup capture, and explicit preset
local-copy authoring are implemented. Server revision 31 retains
`strategy_authoring_v1`, `strategy_authoring_specialized_editors_v1`, and
`strategy_authoring_profile_lifecycle_v1`, `strategy_action_gate_v1`, and every
older capability, retains revision-23 `strategy_revision_history_v1`, and adds
`strategy_authoring_preset_local_copy_v1`. Revision 30 added
`save_backed_setup_capture_v2` while retaining revision-29
`save_backed_setup_capture_v1`; revision 25 added
`managed_custom_module_presets_v1` after revision 24 added
`strategy_authoring_local_loadout_editors_v1`. The original profile endpoint
remains the compatibility facade. The native shell handles every
registered editor type with server-declared managed controls or an honest fixed
presentation, retains dormant Ignore values and unknown Ultimate Weapon
fields, and keeps validation, resolution, publication, and runtime authority
in Python. The runtime-owned Strategy Gate snapshot and native banner are
separate from requested and acknowledged Pause.

Custom Strategy display-name changes use ordinary reviewed publication, so the
stable ID does not change and the next logical version is published under the
same optimistic fingerprint protection as any other edit. Recoverable deletion
is a separate `retire_strategy` operation: Linux validates the fixed ID and
fresh source fingerprint, refuses bundled/reserved or currently selected
Strategies, and atomically moves the exact publication into the store-owned
`retired` directory. It then refreshes both authoring and legacy active
catalogs and audits the retirement without changing selection or activation.
The immutable history remains discoverable after retirement. **History** opens
server-computed revision details and comparisons for active and retired
lineages; a successful explicit restore review publishes the historical intent
as the next version and refreshes history/latest catalogs without selecting or
activating it. The retirement archive remains evidence rather than a competing
editable rollback model.
The profile-local backend contract, custom Module catalog, captured-draft
reopen path, and additive API/WPF editing above are implemented. The expanded
disposable-catalog Windows runtime smoke remains the separate follow-up slice.
