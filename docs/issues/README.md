# Issue Records

The compact [`../observed_issues.md`](../observed_issues.md) index is canonical
for active lifecycle classification and routing. It identifies exactly when a
full dossier must be loaded and links actionable work to the owning backlog.

## Active evidence

- [`open-2026.md`](open-2026.md) preserves complete active observations,
  evidence, safety responses, repairs, recurrences, commits, regressions, and
  remaining requirements. Load only the dossier selected by the active index.

## History

- [`resolved-2026.md`](resolved-2026.md) preserves complete fixed symptoms,
  causes, resolutions, commits, regressions, and operational lessons.
- [`unconfirmed-2026.md`](unconfirmed-2026.md) preserves unreproduced,
  non-actionable observations without misclassifying them as resolved.

## Durable evidence

- [`evidence/`](evidence/README.md) contains narrow tracked extracts needed by
  issue dossiers when a production source is subject to rolling retention. It
  is not a general runtime-artifact archive.
