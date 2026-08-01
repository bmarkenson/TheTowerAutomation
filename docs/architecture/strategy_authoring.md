# Strategy Authoring Architecture

This document defines the target contract for GUI-authored strategy bases and
strategies. It is an architecture boundary, not a claim that every part is
implemented. The current Farm profile format remains supported while this
model is introduced incrementally.

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

Accepting the rebase updates the strategy draft and requires normal validation
and publication. Canceling it changes nothing. Publication and activation
remain separate operations, so a newly published revision cannot silently
replace the active runtime strategy.

## Runtime gates and action authority

At a Home/new-run boundary, an enforced mismatch blocks the strategy from
advancing past the applicable configuration gate unless a verified repair
owns and confirms the transition.

If an enforced mismatch is discovered in an already running battle, the
runtime must not express that condition as global Pause. It creates a strategy
gate with three authority classes:

1. Capture, detection, state interpretation, and evidence collection continue.
2. Explicitly allowlisted independent auxiliary collectors, such as a safe Gem
   collection whose preconditions do not depend on the invalid strategy
   setting, may continue.
3. Strategy-dependent and lifecycle actions remain blocked until the gate is
   resolved or the operator changes strategy policy.

The gate does not authorize Surrender, Exit Battle, restart, or a manufactured
run boundary. The control surface should state the distinction directly, for
example: “Strategy actions blocked — observation and safe collectors remain
active.” Existing global Pause remains stronger and continues to block every
handler action while allowing capture and detection.

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
- A strategy whose base has a newer revision shows an update banner and opens
  the reviewed rebase diff before changing anything.
- Review & Publish displays source changes, resolved changes, validation
  results, generated-plan identity, and whether the active strategy will remain
  unchanged after publication.

The API returns the same source, policy, resolution, provenance, capability,
and validation vocabulary so the WPF client does not duplicate resolver rules.

## Code ownership

Implementation should introduce these responsibilities without moving them
into the runtime evaluator:

- A setting registry owns metadata, normalization, validation, dependencies,
  and adapters for all authorable settings.
- A generic resolver combines a sparse base snapshot and sparse strategy source
  into resolved values plus provenance.
- A versioned base store owns immutable base revisions, catalog reads, stale
  write protection, and atomic publication.
- `StrategyProfileStore` (or a narrowly separated authoring store behind it)
  owns strategy drafts and publications, embeds the pinned base snapshot, and
  retains the existing fixed-directory and fixed-filename safety boundary.
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
   setting, then perform Windows GUI smoke validation.
4. Runtime strategy-gate refinement: implement and validate the distinct
   observation, auxiliary-collector, and strategy-action authority classes
   before relying on running-battle enforcement from newly editable settings.

Each slice should be completed and validated in its own development thread.
The actionable sequence is tracked in the
[`runtime and validation backlog`](../backlog/runtime-and-validation.md).
