# Issue Records

The compact [`../observed_issues.md`](../observed_issues.md) index owns active
lifecycle classification and routing. It tells a reader exactly when to load a
full dossier and links actionable work to one domain backlog.

## Record contract

Give every issue a stable ID. Each active-index entry contains only its status,
one-sentence symptom or hazard, required safety behavior, dossier-load
condition, next evidence requirement, dossier link, and backlog link. Put the
complete dated observation, evidence, response, analysis, implementation
status, recurrences, commits, tests, and unresolved requirements in the
matching [`open-2026.md`](open-2026.md) dossier.

When one report separates into independently open and resolved causes, assign
each lifecycle its own stable ID and cross-link the records.

## Transitions

- **Resolved:** retain the original symptom; add cause, resolution, fixing
  commit, and regression location; move the complete dossier to
  [`resolved-2026.md`](resolved-2026.md); remove its active-index entry. The
  dossier owns the detail; add only a concise link in the completed-task log.
- **Unconfirmed:** move an unreproduced, non-actionable report to
  [`unconfirmed-2026.md`](unconfirmed-2026.md) without calling it fixed.
  Preserve negative and later-success evidence, tests, and the exact evidence
  required on recurrence; remove active routing until a matching recurrence.
- **Reopened:** restore compact active routing and move or copy only the
  lifecycle evidence necessary to the open dossier, preserving the history
  link rather than rewriting it.

Add each new yearly dossier or history file to this index.

## Durable evidence

[`evidence/`](evidence/README.md) owns narrow tracked extracts cited by issue
dossiers when their production source rolls off. Preserve only cited rows and
fields, definitions, units, exact query windows, source identity, and a
reproducible read-only extraction method—not a full production artifact.

A regression fixture should own evidence needed by behavior tests. Generated
evidence that must remain under a runtime cleanup root also needs its exact path
in `config/protected_artifacts.txt`; documentation links do not exempt cleanup.
