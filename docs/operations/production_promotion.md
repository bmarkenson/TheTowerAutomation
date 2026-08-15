# Production Promotion and Rollback

Production services remain fixed to the `main` checkout. The
[outcome-coordination default](../new_thread.md#outcome-coordination) assigns
the promotion owner and defines operator opt-outs. Unless narrowed there,
ownership continues through exact candidate validation and promotion,
applicable non-Git deployment and smoke, `origin/main` publication, and default
clean integrated retirement. It never includes publishing tags or temporary
refs. The exact candidate comes from a temporary feature branch, a temporary
integration branch only when several feature tips must ship together, or the
allowlisted private save-mapping staging ref. Complete the repository-change
checklist before this procedure and [`live_preflight.md`](../live_preflight.md)
before any service/device action.

When the default applies, a validated feature commit is an intermediate
candidate, not task completion; do not stop to request separate promotion
authorization.

## Prepare one exact candidate

Do not rewrite, rebase, or otherwise move in-flight feature work merely because
its history began from an older `main` or the former standing `develop` branch.
Commit and preserve that work in its owned worktree, then select the smallest
candidate boundary that fits the outcome:

Do not mechanically merge every branch that is not yet an ancestor of `main`.
First inspect its exact tip, ownership, remaining work, and aggregate difference
from current production; retain active work and classify already integrated,
superseded, or ambiguous work through the retirement procedure rather than
turning branch cleanup into a release.

1. For one coherent feature, use its clean committed `feature/<outcome>` tip as
   the candidate. Bring current `main` into that branch when needed, resolve and
   review there, and require `main` to be an ancestor before promotion.
2. When two or more reviewed feature tips intentionally form one release, the
   assigned outcome coordinator creates a temporary `integration/<outcome>`
   branch and worktree from current `main`, integrates only those exact tips,
   and resolves combined changes there. Do not use a standing integration
   branch or pull unrelated ready work into the candidate.
3. Record the candidate branch or private ref, worktree when one exists, and
   exact tip `D`. Freeze that candidate while its applicable gate and promotion
   are in progress. Changed candidate content or a changed tip requires a new
   exact `D` and renewed review; a new object ID alone does not invalidate every
   completed check. If production `main` advances while `D` stays exact,
   discard the recorded `M` and repeat the ancestry and aggregate `M..D`
   review. If the new `main` is not an ancestor of `D`, reconcile in the
   candidate worktree; that reconciliation creates a new `D`.

   Treat a multi-command gate as composable evidence. A check or component
   completed at prior exact candidate `V` may carry to refreshed `D` when
   review of `V..D` proves that the inputs it exercises and any relevant
   production baseline are unchanged. If the delta affects only some
   components, retain the unaffected results and rerun only the affected
   scope; do not repeat a wrapper merely to obtain one all-at-`D` label.
   Record `V`, `D`, the reviewed delta, each retained result, and each fresh
   result. When input equivalence or baseline independence is uncertain,
   broaden only the affected scope enough to resolve that uncertainty.

Unrelated branches and worktrees may remain dirty or continue independently;
they block promotion only if their work is included in, overlaps, or obscures
the selected candidate.

## Choose the candidate gate

Validation follows the aggregate `M..D` change and remaining uncertainty, not
the number of branches or Git operations used to produce it. Select the
minimum sufficient gate that exercises the affected contract. A complete
checkpoint is not a generic confidence or closeout step: its extra compilation,
state-definition, clickmap, and full-suite coverage is justified only by a
listed production-consumed or cross-cutting validation risk. Run focused tests,
generators, static checks, and native builds while the candidate can still
change. Finish and commit every source, test, configuration, generated, and
other gate input before running the selected final gate. When several feature
tips form one release, validate each proportionately while developing and run
the combined gate once on the final integrated candidate.

| Aggregate candidate contents | Required candidate gate |
| --- | --- |
| Documentation, process guidance, completion evidence, or test source, fixtures, and expected output not consumed by runtime | Link/diff/static checks and affected tests. A shared test-harness change may require broader pytest coverage. Do not run the complete checkpoint solely for this row. |
| Only canonical player-save mapping JSON produced by the reviewed fast lane | Exact allowlisted diff, target hashes/modes, mapping schema/set invariants, and focused mapping-loader/consumer tests. Do not run the complete checkpoint solely for this row. |
| Native Windows client inputs with no shared Linux/runtime change | Affected Python/JavaScript contract tests, portable .NET tests, and the Release cross-build. Reserve the state-changing complete-package publisher for the required publication boundary below; do not add the Python checkpoint solely for this row. |
| Shared test, checkpoint, build, packaging, or release machinery with no runtime-input change | Direct contract tests plus only the broad or end-to-end command whose machinery changed. Run one complete checkpoint only when the checkpoint path itself changed or its full path is the affected contract. |
| Runtime Python, shared control-surface code, YAML, templates, runtime-read assets, generators, or broadly consumed configuration | Focused tests first, then one complete repository checkpoint for the committed runtime-input state being promoted. |
| Interpreter, lock files, persistent-state formats, installed units, migrations, or a cross-cutting change whose affected contracts cannot be bounded after review | One complete checkpoint for the committed input state plus the specific rebuild, migration, or recovery proof. Record the unbounded risk that required it. |

Transient or ignored test output is evidence, not a candidate input, and never
selects the complete checkpoint by itself. A test-only change that reveals a
runtime defect selects a runtime row only after a production-consumed input is
changed. If several rows apply, combine their requirements without repeating
unaffected checks.

Record exact `D`, the selected gate and result, and the development-environment
fingerprint when applicable. When using the complete checkpoint, also name the
table row and additional coverage that made it necessary.

### Exact candidate before the final gate

The final candidate gate is not a pre-commit check. First create a clean exact
candidate `V`. While its inputs are mutable, run focused checks only; do not use
the complete checkpoint as a rehearsal. Run a selected complete checkpoint at
most once for unchanged `V`.

Repeat a complete checkpoint only when its prior run failed or was incomplete,
a later change invalidates its production, checkpoint-machinery, or relevant
baseline coverage, or the operator explicitly requests a diagnostic rerun. A
changed commit ID alone does not invalidate it. Documentation, ordinary
test-only changes, transient test output, completion records, ref movement,
contention, checkout, promotion, publication, and cleanup do not by themselves
require another complete checkpoint; retain unaffected component results and
run only their affected checks. If a gate exposes a needed change, create the
corrected exact candidate and apply this invalidation rule to its reviewed
delta.

Before freezing `V`, inspect ignored non-cache output in its worktree. Tests
must use pytest temporary directories or the checkpoint's isolated generated
root for logs, screenshots, control files, and failure evidence; they must not
populate the repository's ignored runtime-evidence paths. Repair and rerun a
leaking test before promotion rather than turning routine retirement into an
ambiguous destructive-evidence decision.

### Completion-record exception

The normal completion-record sequence avoids rerunning an expensive code gate
merely to record its result: commit exact code/test candidate `V`, complete its
gate, then create one immediately following commit `D` that only adds or
corrects the concise completion record. Review `V..D`, run the documentation
checks at final `D`, and verify that the delta changes no source, tests,
configuration, generated or runtime-read input, dependency, unit, or native-
package input and that the earlier gate does not read the record. Record both
commits and both results. Any other change or uncertainty requires the
applicable gate at the corrected exact candidate.

Checking out already validated `D` on `main`, publishing it, or removing its
integrated temporary ref/worktree does not change the candidate and does not by
itself repeat candidate validation. Mutable production rereads, artifact work,
and post-deployment smoke remain separate requirements below.

## Own one mutable promotion transaction

Candidate development and validation may proceed in parallel. Before creating
a rollback tag or changing production `main`, services, published artifacts,
`origin/main`, or the promotion's cleanup topology, atomically acquire the
local private `refs/thetower/promotion-owner` ref for frozen candidate `D`. It
serializes the shared production transaction; the remote fast-forward rule
separately guards publishers from another clone.

1. Inspect the ref first. Its object and reflog identify the in-progress
   candidate and recorded branch/worktree; age alone never proves it stale.
2. When the ref is absent, acquire it with compare-and-create from any linked
   checkout:

   ```bash
   git update-ref --create-reflog \
     -m "promotion <candidate-branch> <candidate-worktree>" \
     refs/thetower/promotion-owner <D> \
     0000000000000000000000000000000000000000
   ```

   A failed compare-and-create means another transaction won the race and is
   handled as contention in step 3.
3. When the ref already exists or acquisition fails, make no production or
   promotion-topology mutation. Contention is a waiting state, not promotion
   failure or task completion. Inspect
   `git reflog show -1 refs/thetower/promotion-owner`, retain the candidate, and
   wait read-only with bounded checks and visible status until the ref clears.
   Do not retry around, time out, or auto-clear the recorded owner. Once it
   clears, do not resume from the earlier gate:

   a. Reread the candidate branch, tip, and cleanliness; production `HEAD`,
      `main`, and cleanliness; and the live remote `main` tip.
   b. Reconcile current production `main` into the candidate when necessary,
      record the resulting exact `D`, and review the new aggregate `M..D`.
   c. Apply the validation-reuse rule above to the prior exact candidate and
      refreshed `D`. Rerun checks invalidated by changed inputs or the old
      production baseline; do not repeat a complete checkpoint solely because
      contention occurred or reconciliation changed only the candidate object
      ID. Uncertain equivalence still selects the strongest applicable gate.
   d. Recheck the owner ref and return to step 2. If another transaction wins,
      repeat this wait, refresh, and gate loop; do not abandon the outcome.

4. Verify the ref equals `D` and keep it unchanged through successful-
   promotion closure. If a changed `main` requires candidate reconciliation,
   compare-delete the ref at exact old `D` and return to step 3's refresh and
   gate path; never repoint an active ownership ref.
5. If the recorded owner cannot finish, only an explicitly assigned recovery
   owner may inspect candidate, production, remote, deployment, and cleanup
   state, bring them to a recorded coherent boundary, resolve the retirement
   inventory, and use its guarded close. A missing or corrupt legacy inventory
   is recovery evidence, not permission to skip source-topology reconciliation.
   Never auto-clear the ref or delete it merely to unblock another promotion.

## Promote one exact candidate

Enter this section only while `refs/thetower/promotion-owner` names exact `D`.

1. Require clean candidate and production worktrees. Any unclear production or
   candidate change, staged file, unmerged entry, or unresolved nonignored
   untracked file blocks promotion. For an integration candidate, also record
   every accepted source branch and exact source tip.
2. Record production commit `M` and candidate commit `D`. Recheck both refs and
   prove that `M` is an ancestor of `D`.
3. Before any production mutation, create the exact candidate's durable
   retirement inventory. Repeat `--source` for every delegated branch accepted,
   rejected, or still under consideration for this outcome:

   ```bash
   /usr/bin/python3.12 tools/outcome_retirement.py begin \
     --base <M> --candidate <D> --candidate-ref <candidate-ref> \
     --source <delegated-branch>
   ```

   The standard-library tool atomically stores each source branch, linked
   worktree, and tip under the common Git directory at
   `thetower/outcome-retirement/<D>.json`. It also scans local branches for tips
   newly ancestral in `D` and for source patches absent from `M` but represented
   in `D`; the latter is the `+` at `M`, `-` at `D` transition reported by
   `git cherry`. Discovery supplements rather than replaces the explicit source
   list. Every entry starts pending, including a patch-equivalent cherry-pick
   whose commit hash changed. Even an outcome with no delegated source creates
   an empty inventory, so ordinary closure has one machine-checked path.
4. Review all `M..D` commits and the aggregate diff. Verify the applicable
   candidate gate above is complete for exact `D`, including the recorded
   completion-record exception when used. Resolve remaining uncertainty with
   retained or live evidence as appropriate. Classify every publishable
   Windows-package input in that diff; a source checkout update does not publish
   the native client. Unless the operator explicitly withheld remote
   publication, read the live `origin` `refs/heads/main` tip, require it to be
   absent or an ancestor of `D`, and stop before local promotion if the
   candidate contains anything known to be unsuitable for that remote.
5. For every candidate except documentation-only, create a unique annotated
   local tag at `M`, for example
   `production-before-20260804T210500Z-fe3c83b`. Never move or reuse it;
   pushing a tag is a separate operator decision. A documentation-only
   promotion creates no rollback tag: its parent remains in ordinary `main`
   history and no runtime or non-Git deployment state changes.
6. Recheck that the candidate still names `D` and production still names `M`,
   select the boundary below, fast-forward the production checkout to exact
   object `D`, and verify `HEAD == main == D`. Abort on a non-fast-forward,
   changed ref, or newly dirty checkout.
7. For documentation-only candidates, treat step 6's exact-commit and clean-
   worktree verification plus the exact-`D` candidate gate as the complete
   post-promotion verification; perform no separate content/link/static smoke
   and no service or runtime action. For every other candidate, apply
   only separately reviewed non-Git changes, restart affected services, and
   perform a bounded production smoke test. Record the promoted or deployed
   commit and result.
8. Complete the [successful-promotion closure](#close-a-successful-promotion).
   Promotion ownership includes default publication of exact `D` to
   `origin/main` and retirement of the outcome's clean integrated temporary
   branches and worktrees. Retain one only when an explicit request or a
   guard in the retirement procedure applies.

| Candidate contents | Production boundary |
| --- | --- |
| Documentation only | Use steps 6–8 without a rollback tag, service stop, restart, or runtime smoke. |
| Runtime Python, YAML, templates, or runtime-read assets | Stop automation before update; restart and smoke-test afterward. |
| Control surface or shared modules | Stop/restart the control-surface service; also stop automation when shared runtime code changes. |
| Native Windows package input | Complete the [required Windows package publication](#required-windows-package-publication) after the production checkout reaches `D`. |
| Interpreter or locked dependencies | Stop every affected service and retain the prior environment or a proven rebuild path through smoke validation. |
| Installed unit or persistent-state format | Treat installation/migration as a separately reviewed operation with recovery recorded first. A checked-in unit change does not install itself. |

### Direct save-mapping staging

The control-surface save-mapping action stages one exact Git object; it does not
move `main` or touch the production index or worktree. Its routine lane is
available only when production is a clean `main` checkout at its tip and
`refs/thetower/save-mapping-candidate` is empty. In either GUI, select the
durable observation, inspect **Private staging eligibility**, review the exact
proposal and target hashes, then confirm **Stage reviewed mapping for
promotion…**.

A Module `infoIndex` proposal adds only its global name/family identity; the
observed slot remains pairing provenance and is not patched. Once ordinary
promotion integrates that identity into both canonical mapping owners, the
versioned `canonical_global_same_family` contract permits exact save matching
in either slot of that family. The reviewed identity therefore can suppress
future duplicate UI observation, while a local confirmation or staged private
candidate cannot. Unknown names/IDs, cross-family placement, duplicate
installed names, malformed structure, and mismatches retain the full Modules
UI route.
Explicit empty assignments are not identity candidates: only the versioned
`moduleEquipped` array null or exact unlocked Assist `equippedModule=null`
shape maps to canonical `empty`. Missing fields, missing slots, locked Assist
slots, and
visual `not_ancestral` results are not promoted as empty evidence.

The review fingerprint binds the mapping proposal, canonical target
before/after hashes and modes, mapping-set fingerprint, candidate identity, and
commit-message contract. It intentionally does not bind unrelated files or the
whole `main` commit. On confirmation the server repeats that review against
current `main`. If the mapping inputs are unchanged, it constructs the commit
with a private index using current `main` as parent, verifies that only the
allowlisted mapping JSON paths differ, and atomically creates the fixed private
ref while verifying `main` did not move. Thus an unrelated `main` advance
before confirmation does not force a new operator review, while the resulting
candidate is still a direct fast-forward child of the current tip.

Success must identify the fixed staging ref, actual base, staged commit, exact
canonical target hashes, passed mapping invariants, `committed=true`,
`staged=true`, and `promoted=false`. Treat that exact staged object as
candidate `D`: inspect its diff and provenance trailers, run the mapping-only
candidate gate, then continue at step 2 of this production procedure. Do not
substitute a full repository checkpoint merely because the object is being
promoted.

Do not clear the persistent warning when staging succeeds. It becomes **Save
mapping awaiting production promotion**, then **Deployed save mapping awaiting
fresh validation** after `main` contains the commit, and retires only when a
later complete stable save proves the running decoder loaded the matching
canonical mapping set. The application then removes the exact private ref and
journal; the commit remains reachable from `main`. That passive observation
cannot change runtime authority or send input.

A stale mapping review, target/hash/mode drift, dirty production worktree,
occupied staging ref, busy lock, or other proven pre-write rejection requires a
fresh catalog and review; never retry automatically. If the catalog reports
exact integration recovery, only the same durable candidate remains actionable:
inspect its stored target hashes and fingerprint, then invoke the staging action
once to continue its transaction. A response lost after the ref was created
reappears as the same promotion-pending commit rather than another write.

If `main` advances after staging without containing the candidate, the old
object is no longer a fast-forward candidate and the status reports
`restaging_required`. Do not merge, cherry-pick, or promote it. Inspect and
retire only that exact stale transaction/ref through a reviewed recovery, then
review the unchanged mapping inputs and stage a new child of current `main`.
A malformed or legacy journal, moved ref, target supersession, or any outcome
that cannot be proved exact is unconfirmed: do not edit targets or move refs
ad hoc; route the repair through ordinary owned development.

### Required Windows package publication

Any promotion whose aggregate `M..D` diff changes an input to either published
Windows executable must also publish the complete native Windows package.
Inputs include application source, XAML, project files, resources, and publish
tooling under `windows/TheTower.ControlSurface`, `windows/TheTower.TunnelHost`,
`windows/TheTower.TunnelHost.Core`, and `windows/TheTower.TunnelProtocol`;
documentation-only and test-only changes do not activate this boundary.

After verifying that the production checkout is exactly `D`, follow the native
client's canonical [complete-package publisher](../../windows/TheTower.ControlSurface/README.md#publish).
That workflow owns staging, complete-package verification, guarded replacement,
rollback-slot rotation, and transaction recovery; do not reproduce its
mechanics here. Require adjacent, nonempty `TheTower.ControlSurface.exe` and
`TheTower.TunnelHost.exe` files in current and every retained slot. Do not copy
only one executable, mix files from different slots, publish from a different
commit, or treat portable tests, the candidate cross-build, or an earlier
package as satisfying the current-publication boundary.

Before reporting the promotion complete, record `D`, the publication time,
size, and SHA-256 digest of both current executables. Also inventory every
retained slot and record both executable sizes and digests, associating it with
its prior production commit when the existing publication record proves that
mapping. A failed or unverified publication or package rotation blocks the
production-success claim even when the Linux deployment and smoke test pass.
Cross-publication does not establish WPF runtime behavior; follow the native
client's
[Windows-only lifecycle validation](../../windows/TheTower.ControlSurface/README.md#windows-only-lifecycle-validation)
before describing a package as deployed and validated on Windows.

### Native Windows package rollback

If a post-publication Windows defect requires immediate artifact rollback,
close the affected GUI and follow the native publisher's
[complete-package rollback rule](../../windows/TheTower.ControlSurface/README.md#publish),
selecting one retained slot by its recorded hashes. Record the chosen slot,
hashes, associated source commit when known, destination, and Windows smoke
result. This is a bounded artifact recovery, not a source rollback: create the
normal reviewed revert or fix-forward on a temporary feature branch from
current `main`, validate and promote that exact candidate, and republish from
the resulting production commit so `publish/win-x64` again matches production
source.

## Close a successful promotion

Publish the exact successful `main` tip to `origin/main` as part of promotion
unless the operator explicitly requests no publication. Known nonpublishable
content, an unexpected remote non-fast-forward, or a network/authentication
failure also stops that step; none authorizes a force-push, rewritten history,
or a different destination. Publication and retirement are normal closure
duties of the promotion owner. After the applicable post-promotion verification
or smoke check succeeds:

1. Recheck that production `HEAD` and `main` still equal exact candidate `D`,
   that `D` remains reachable from the retained candidate branch, and that the
   production and candidate worktrees are clean. Record the durable completion
   and validation evidence before removing any temporary ref or checkout. The
   commit that records an outcome is part of that outcome and needs no recursive
   completion entry. If tracked post-deployment evidence is still required,
   commit it on the retained candidate branch or a new documentation-only
   feature branch and promote that exact follow-up candidate; never commit it
   directly in production. Keep publication and retirement pending until that
   follow-up is integrated, then publish the final exact `main` once. Use the
   completion-record exception only when that follow-up meets its narrow terms.
2. Unless publication was explicitly withheld, reread the live remote `main`
   tip, require it to be absent or an ancestor of `D`, and push only the
   explicit fast-forward refspec `refs/heads/main:refs/heads/main`. Verify the
   live remote tip equals `D` afterward and reconcile the local remote-tracking
   ref. A normal push publishes every commit reachable from `D`, with its
   existing ancestry and metadata; it is not a tip-only snapshot. On a changed
   or non-fast-forward remote, known nonpublishable content, or a
   network/authentication failure, leave local `main` at `D`, report the exact
   unpublished state, and do not retry through a force or alternate ref.
3. Do not automatically publish rollback tags, archive tags, temporary
   branches, or bundles with `main`. A remote feature or integration branch is
   the supported way to publish branch-only interim commits, while tag
   publication remains a separate operator decision. Deleting any remote ref
   is also a separate exact-target decision.
4. Give every feature and integration branch involved in the outcome one
   explicit disposition: integrated and eligible for normal retirement;
   explicitly superseded or abandoned and eligible only for archived
   retirement; or retained/deferred with its owner and remaining work recorded.
   Ambiguity always selects retained/deferred. Record each delegated source in
   the inventory with `tools/outcome_retirement.py disposition`: `integrated`
   accepts only a tip ancestral to `D`; `superseded` requires the exact annotated
   `archive/...` tag and a reason; and `retained` requires an owner and remaining-
   work description. Patch equivalence is evidence for supersession, never exact
   integration.
5. Apply the retirement procedure below to every clean exact integrated
   temporary branch/worktree involved in the outcome. Retain an object when the
   operator requests it, ownership is active or unclear, the worktree contains
   unique changes or evidence, ancestry is ambiguous, or a non-force cleanup
   command refuses it. Superseded or abandoned work uses its separate explicit
   disposition. Recheck immediately before each mutation and finish by
   re-listing the topology. A withheld or failed remote publication does not by
   itself make an integrated branch unique or prevent safe retirement.
6. After publication disposition, retirement, and final topology verification
   are complete, use the guard to release only this transaction and inspect the
   result:

   ```bash
   /usr/bin/python3.12 tools/outcome_retirement.py close --candidate <D>
   git rev-parse --verify --quiet refs/thetower/promotion-owner
   ```

   `close` rescans for newly represented source patches, durably adds any newly
   found source as pending, and refuses to release ownership while an entry is
   pending, an integrated/superseded pair remains, an archive is invalid, or a
   retained pair no longer preserves its inventoried tip. Only then does it
   compare-delete the exact owner ref; the completed JSON inventory remains as
   local transaction evidence. Never directly delete the owner ref during
   ordinary closure. No ref confirms the promotion lane is idle; if another ref
   is already present, a later transaction acquired the lane after release and
   must remain untouched. If active recovery still requires production mutation,
   retain the ownership ref and hand off that exact state instead of allowing a
   second promotion to overlap it.

## Retire temporary work

The [repository topology](../architecture/development_isolation.md#repository-and-git-topology)
keeps only the production `main` branch and checkout permanent. Feature and
integration branches and worktrees are temporary. Retirement has separate
integrated and superseded dispositions; never describe patch-equivalent or
selectively ported work as integrated.

Before any disposition:

1. Re-list every local branch and linked worktree. Recheck the candidate's
   branch and `HEAD`, staged and unstaged changes, nonignored untracked files,
   ignored files that could contain operator work or required evidence, and
   active ownership.
2. Record the exact worktree path, local branch, tip commit, disposition, and
   integration or replacement target. Exclude `main`, rollback tags, remote
   branches, every active candidate, and every ambiguous item; remote deletion
   is always a separate decision. The candidate inventory owns these fields for
   every delegated source; do not rely on chat or the aggregate branch name.

Routine environment links, caches, and build products may be discarded with an
otherwise clean integrated worktree. Logs, screenshots, control files, and
evidence are not presumed disposable merely because Git ignores them. Tests
that intentionally create those files must redirect them to their temporary
test root; if a leak is discovered here, classify its exact producer and
content before requesting deletion or retaining the pair for repair.

### Integrated feature or integration branch

Use this disposition only after promotion succeeds and the outcome's required
validation and evidence are durable. It is the default for every clean exact
pair involved in the promoted outcome. A possible later follow-up is not a
retention reason: the integrated commit is on `main`, and a new branch and
worktree can be created from current `main` when needed.

1. Prove the branch tip is an ancestor of `main`. A merged label or patch-
   equivalent cherry-pick does not override uncertainty or the
   `git branch -d` ancestry guard.
2. Record `--disposition integrated` for the exact branch in candidate `D`'s
   inventory.
3. Run `git worktree remove <exact-path>` and then
   `git branch -d <exact-branch>`. Never recursively delete a worktree or use a
   force option; retain any refused pair for review.

### Explicitly superseded or abandoned temporary branch

Use this disposition only when the operator explicitly declares the exact
local tip obsolete, rejected, or replaced and its disposition is already clear
from durable repository history. It removes branch/worktree clutter without
pretending the discarded commit was integrated:

1. Create a uniquely named annotated `archive/...` tag at the exact branch tip
   and verify that the tag object dereferences to that commit. Never move or
   reuse the tag; pushing it is a separate operator decision. Deleting the
   archive tag or making the commit unreachable is outside this procedure. A
   Git bundle is useful supplementary recovery but does not replace this
   durable in-repository archive ref.
2. Record `--disposition superseded`, `--archive-tag <archive-tag>`, and a
   concise `--reason` for the exact branch in candidate `D`'s inventory.
3. Recheck that the branch, worktree, tip, ownership, and inspected content are
   unchanged and that the verified archive tag still names the tip.
4. Run `git worktree remove <exact-path>` without `--force`. If Git refuses,
   retain the worktree and stop for review; never delete it recursively.
5. Run `git branch -D <exact-branch>` only for the approved, now-unlinked local
   branch and only while its verified archive tag remains. No other force-
   deletion path is authorized.

### Retained or deferred temporary branch

Use this disposition for active, dirty, unique, or ambiguous work. Record its
owner and remaining work with `--disposition retained`, `--owner`, and
`--remaining-work` in the candidate inventory, then leave its branch,
worktree, tracked state, ignored evidence, and dirty state untouched. The
inventoried tip must remain reachable from the retained branch at closure; new
descendant commits are allowed and their exact closing tip is added to the
inventory. A retained pair is resolved for this outcome, not declared complete.

After any disposition, re-list branches and worktrees, verify `main` remains
unchanged and clean, verify every retained checkout preserves its prior state
(including pre-existing dirtiness), and preserve rollback and archive tags. Do
not rerun candidate validation solely because its integrated or archived
worktree/ref was removed.

### Obsolete standing integration branch

A former standing integration branch such as `develop` has no candidate
authority under this procedure. Its presence does not require rebasing or
rewriting any feature branch: those branches retain their commits and reconcile
with current `main` only when selected for a candidate.

Retire the obsolete branch and worktree only after the replacement policy is on
`main`, no promotion or other owner is using the checkout, both are clean, and
the exact branch tip is an ancestor of `main`. Recheck that the branch has no
unique commits, record the exact ref and worktree path, and obtain operator
approval for those objects. Then use non-force `git worktree remove` followed by
`git branch -d`; if either command refuses, retain both for review. Re-list the
topology afterward. A branch with unique, dirty, actively owned, or ambiguous
work is not obsolete and must remain untouched.

## Failed smoke test

1. Stop the affected service, if any, before changing files or environments
   again. Documentation-only content/static failure has no service action.
2. Reinspect production and the recorded `M`, `D`, any rollback tag, and any
   new operator-owned work.
3. Create a temporary recovery feature branch and worktree from current `main`.
   There, create and review a normal revert commit for the promoted range, or a
   smaller fix-forward when it is clearer and equally quick. Do not commit in
   the production checkout or silently move `main` backward.
4. Run the applicable candidate gate for that exact recovery object and
   fast-forward `main` under the same clean-candidate rules before restarting.
5. Restore a prior environment, installed unit, or persistent data only when
   that item changed during deployment, then restart and repeat the smoke test.

The branch and threat-model rationale is in
[`architecture/development_isolation.md`](../architecture/development_isolation.md#staging-promotion-and-rollback).
