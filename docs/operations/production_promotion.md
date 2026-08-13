# Production Promotion and Rollback

Production services remain fixed to the `main` checkout. Promotion is operator-
or explicitly assigned promotion-owner work. The exact candidate comes from a
temporary feature branch or, only when several feature tips must ship together,
a temporary integration branch. Complete the repository-change checklist before
this procedure and [`live_preflight.md`](../live_preflight.md) before any
service/device action.

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
3. Record the candidate branch, worktree, and exact tip `D`. Freeze that branch
   while its complete checkpoint and promotion are in progress. Any new commit,
   ref movement, conflict resolution, or unexpected advance of `main` before
   the guarded production fast-forward invalidates the candidate and requires
   renewed review and validation.

Unrelated branches and worktrees may remain dirty or continue independently;
they block promotion only if their work is included in, overlaps, or obscures
the selected candidate.

## Promote one exact candidate

1. Require clean candidate and production worktrees. Any unclear production or
   candidate change, staged file, unmerged entry, or unresolved nonignored
   untracked file blocks promotion. For an integration candidate, also record
   every accepted source branch and exact source tip.
2. Record production commit `M` and candidate commit `D`. Recheck both refs and
   prove that `M` is an ancestor of `D`.
3. Run the complete checkpoint at `D` from the candidate worktree; review all
   `M..D` commits and the aggregate diff. Resolve remaining uncertainty with
   retained or live evidence as appropriate. Classify every publishable
   Windows-package input in that diff; a source checkout update does not publish
   the native client.
4. Create a unique annotated local tag at `M`, for example
   `production-before-20260804T210500Z-fe3c83b`. Never move or reuse it; pushing
   a tag is a separate operator decision.
5. Recheck that the candidate still names `D` and production still names `M`,
   select the boundary below, fast-forward the production checkout to exact
   object `D`, and verify `HEAD == main == D`. Abort on a non-fast-forward,
   changed ref, or newly dirty checkout.
6. Apply only separately reviewed non-Git changes, restart affected services,
   and perform a bounded production smoke test. Record the deployed commit and
   result.
7. Complete the [successful-promotion closure](#close-a-successful-promotion).
   Deployment does not implicitly authorize a remote push, tag publication,
   branch deletion, or worktree removal.

| Candidate contents | Production boundary |
| --- | --- |
| Documentation only | Fast-forward without stopping automation. |
| Runtime Python, YAML, templates, or runtime-read assets | Stop automation before update; restart and smoke-test afterward. |
| Control surface or shared modules | Stop/restart the control-surface service; also stop automation when shared runtime code changes. |
| Native Windows package input | Complete the [required Windows package publication](#required-windows-package-publication) after the production checkout reaches `D`. |
| Interpreter or locked dependencies | Stop every affected service and retain the prior environment or a proven rebuild path through smoke validation. |
| Installed unit or persistent-state format | Treat installation/migration as a separately reviewed operation with recovery recorded first. A checked-in unit change does not install itself. |

### Direct save-mapping integration

The control-surface save-mapping action commits to `develop`; it does not
promote. Its routine lane is available only when the standing production and
integration worktrees are clean, at their branch tips, and exactly
synchronized. In either GUI, select the durable observation, inspect the fixed
Develop eligibility panel, review the exact proposal and repository
fingerprint, then confirm **Integrate reviewed mapping into develop…**. Success
must identify one standardized commit, exact canonical target hashes, passed
mapping invariants, `committed=true`, and `promoted=false`; production `main`
must remain at the reviewed base.

Treat that commit as the next `develop` candidate. Review its exact diff and
provenance trailers, run the complete supported checkpoint, then continue at
step 2 of this production procedure. This narrow lane does not replace the
combined checkpoint, annotated rollback tag, service boundary, native-package
publication when applicable, or production smoke test.

Do not clear the persistent warning when the `develop` commit succeeds. It
becomes **Save mapping awaiting production promotion**, then **Deployed save
mapping awaiting fresh validation** after `main` contains the commit, and
retires only when a later complete stable save proves the running decoder
loaded the matching canonical mapping set. That observation is passive and
cannot change runtime authority or send input.

A stale review, dirty worktree, unequal or changed Git tip, target/hash/mode
drift, busy lock, or other proven pre-write rejection requires a fresh catalog
and review. Never retry automatically. If the catalog reports exact integration
recovery, the GUI makes only that same candidate reviewable; inspect its stored
target hashes and fingerprint, then invoke Integrate once to continue its
durable transaction. A response lost after the commit was fully recorded
reappears as the promotion-pending commit rather than another write. If the
catalog reports an unconfirmed result, malformed or legacy
journal, moved ref, unrelated index/worktree state, or any outcome that cannot
be proved exact, do not retry, edit targets, or move refs; inspect the recorded
transaction and repository state and route the repair through ordinary
development.

### Required Windows package publication

Any promotion whose aggregate `M..D` diff changes an input to either published
Windows executable must also publish the complete native Windows package.
Inputs include application source, XAML, project files, resources, and publish
tooling under `windows/TheTower.ControlSurface`, `windows/TheTower.TunnelHost`,
`windows/TheTower.TunnelHost.Core`, and `windows/TheTower.TunnelProtocol`;
documentation-only and test-only changes do not activate this boundary.

After verifying that the production checkout is exactly `D`, run the supported
[`publish-linux.sh`](../../windows/TheTower.ControlSurface/publish-linux.sh) or
Windows `publish.ps1` workflow. It must stage and verify a complete
self-contained package containing adjacent, nonempty
`TheTower.ControlSurface.exe` and `TheTower.TunnelHost.exe` files before its
guarded replacement of `windows/TheTower.ControlSurface/publish/win-x64`.
The same operation must retain the former current package as
`publish/previous/1`, move the former slot 1 to `publish/previous/2`, and prune
only older packages after the new current package verifies successfully. A
first or second publication may have fewer prior slots; every present slot must
remain a complete two-executable package. Do not copy only one executable,
mix files from different slots, publish from a different commit, or treat
portable tests or an earlier package as satisfying the current-publication
boundary.

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
close the affected GUI, select one retained slot by its recorded hashes, and
deploy that complete directory. Never combine its GUI with another slot's
tunnel host. Record the chosen slot, hashes, associated source commit when
known, destination, and Windows smoke result. This is a bounded artifact
recovery, not a source rollback: create the normal reviewed revert or
fix-forward on a temporary feature branch from current `main`, validate and
promote that exact candidate, and republish from the resulting production
commit so `publish/win-x64` again matches production source.

## Close a successful promotion

Treat deployment, remote publication, and temporary-branch retirement as
separate recorded decisions. After the smoke test succeeds:

1. Recheck that production `HEAD` and `main` still equal exact candidate `D`,
   that `D` remains reachable from the retained candidate branch, and that the
   production and candidate worktrees are clean. Record the durable completion
   and validation evidence before removing any temporary ref or checkout. If
   tracked post-deployment evidence is still required, commit it on the retained
   candidate branch or a new documentation-only feature branch and promote that
   exact follow-up candidate; never commit it directly in production.
2. If the operator elects to publish production, read the live remote `main`
   tip, require it to be an ancestor of `D`, and push only the explicit
   fast-forward refspec `refs/heads/main:refs/heads/main`. Verify the live
   remote tip equals `D` afterward and reconcile the local remote-tracking ref.
   A normal push publishes every commit reachable from `D`, with its existing
   ancestry and metadata; it is not a tip-only snapshot.
3. Do not automatically publish rollback tags, archive tags, temporary
   branches, or bundles with `main`. A remote feature or integration branch is
   the supported way to publish branch-only interim commits, while tag
   publication remains a separate operator decision. Deleting any remote ref
   is also a separate exact-target decision.
4. Give every feature and integration branch involved in the outcome one
   explicit disposition: integrated and eligible for normal retirement;
   explicitly superseded or abandoned and eligible only for archived
   retirement; or retained/deferred with its owner and remaining work recorded.
   Ambiguity always selects retained/deferred.
5. Apply the retirement procedure below only to exact approved objects. Recheck
   branches, worktrees, ignored evidence, and concurrent ownership immediately
   before each mutation, then finish by re-listing the complete topology.

## Retire temporary work

The [repository topology](../architecture/development_isolation.md#repository-and-git-topology)
keeps only the production `main` branch and checkout permanent. Feature and
integration branches and worktrees are temporary. Retirement has separate
integrated and superseded dispositions; never describe patch-equivalent or
selectively ported work as integrated.

Before either disposition:

1. Re-list every local branch and linked worktree. Recheck the candidate's
   branch and `HEAD`, staged and unstaged changes, nonignored untracked files,
   ignored files that could contain operator work or required evidence, and
   active ownership.
2. Record the exact worktree path, local branch, tip commit, disposition, and
   replacement or integration target. Obtain operator approval for those exact
   local objects. Exclude `main`, rollback tags, remote branches, every active
   candidate, and every ambiguous item; remote deletion is always a separate
   decision.

### Integrated feature or integration branch

Use this disposition only after promotion succeeds and the outcome's required
validation and evidence are durable:

1. Prove the branch tip is an ancestor of `main`. A merged label or patch-
   equivalent cherry-pick does not override uncertainty or the
   `git branch -d` ancestry guard.
2. Run `git worktree remove <exact-path>` and then
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
2. Recheck that the branch, worktree, tip, ownership, and inspected content are
   unchanged and that the verified archive tag still names the tip.
3. Run `git worktree remove <exact-path>` without `--force`. If Git refuses,
   retain the worktree and stop for review; never delete it recursively.
4. Run `git branch -D <exact-branch>` only for the approved, now-unlinked local
   branch and only while its verified archive tag remains. No other force-
   deletion path is authorized.

After either disposition, re-list branches and worktrees, verify `main` and
every retained checkout remain unchanged and clean, preserve rollback and
archive tags, and run proportionate repository validation.

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

1. Stop the affected service before changing files or environments again.
2. Reinspect production and the recorded `M`, `D`, tag, and any new operator-
   owned work.
3. Create a temporary recovery feature branch and worktree from current `main`.
   There, create and review a normal revert commit for the promoted range, or a
   smaller fix-forward when it is clearer and equally quick. Do not commit in
   the production checkout or silently move `main` backward.
4. Run the complete checkpoint for that exact recovery candidate and
   fast-forward `main` under the same clean-candidate rules before restarting.
5. Restore a prior environment, installed unit, or persistent data only when
   that item changed during deployment, then restart and repeat the smoke test.

The branch and threat-model rationale is in
[`architecture/development_isolation.md`](../architecture/development_isolation.md#staging-promotion-and-rollback).
