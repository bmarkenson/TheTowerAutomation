# Preparing a Thread Handoff

Read this file only when responsibility must move to another top-level chat. A
new thread otherwise needs no handoff: Codex loads `AGENTS.md`, and every
TheTower thread reads `docs/new_thread.md`.

A handoff is a delta, not a repository briefing. Include:

1. one concrete outcome;
2. task-specific authority or scope boundaries;
3. the assigned checkout and branch;
4. links to only the active documents needed next; and
5. retained evidence paths when non-obvious evidence constrains the task.

Add these only when material:

- **Repository note:** owned or parallel changes that create an overlap or
  ordering concern. Do not paste a generic status; the next thread checks it.
- **Validation:** current results the next thread may rely on. Do not present an
  old test count as a current baseline.
- **Fresh live state:** only after completing `docs/live_preflight.md` during
  handoff preparation. Give timestamp and timezone. Otherwise omit it.
- **Unrecorded follow-up:** only work absent from an active backlog.

Put durable anomalies in `docs/observed_issues.md`, work in its domain backlog,
and stable contracts or procedures in their canonical owners. Do not repeat
stable safeguards, architecture, routine logs, generic commit lists, or stale
control, lock, PID, ADB, or screen facts.

## Template

```text
Continue TheTower work in <assigned checkout> on <assigned branch>.

Outcome:
<one concrete result>

Boundaries:
- <task-specific constraint or authority limit>

Read:
- <exact active backlog, architecture, issue, or procedure link>

Evidence:
- <durable path and what it proves>
- <ephemeral path, labeled as such, only when material>
```

Append only the applicable conditional paragraphs above. Omit empty headings,
placeholder values, and `none` sections.
