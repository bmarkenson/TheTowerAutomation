# Preparing a Thread Handoff

Read this file only when preparing or reviewing a handoff. A new development
thread does not need it: Codex automatically loads `AGENTS.md`, and the handoff
directs the new thread to `docs/new_thread.md`.

## Minimal content

A handoff is a delta, not a repository briefing. Normally it needs only:

1. one concrete task;
2. scope or authority boundaries specific to that task;
3. links to the exact active documentation the next thread needs; and
4. retained evidence paths when the task depends on non-obvious evidence.

Omit a heading when it adds no information. Put durable anomalies in
`docs/observed_issues.md`, actionable work in the relevant domain backlog, and
stable architecture or procedure in its canonical document instead of copying
those facts into the handoff.

## Conditional additions

Add these only when they materially change how the next thread can proceed:

- **Repository note:** task-owned uncommitted or parallel work that creates a
  real preservation, ordering, or overlap concern. Do not dump `git status`;
  the next thread must inspect it freshly.
- **Validation:** current-package results the next thread will rely on. Do not
  reuse an older test count as a present baseline.
- **Fresh live state:** only after completing the mandatory inspection in
  `docs/new_thread.md` during handoff preparation. Include the timestamp and
  timezone. If live state was not inspected, omit this section; the startup
  instruction already requires fresh inspection before live work.
- **Unrecorded follow-up:** only work not already present in an active backlog.

List commits only when they are directly relevant to the task. Distinguish
durable repository evidence from ephemeral `/tmp` material. Never repeat stable
safety rules, broad architecture explanations, generic commit lists, or stale
control/lock/PID/ADB/screen facts.

## Template

```text
Continue TheTower development in /home/brianm/dev/python/TheTower.

Follow the automatically loaded AGENTS.md and read docs/new_thread.md. Choose
the smallest applicable startup path, and complete its live-runtime path before
any process/device interaction or claim about volatile runtime state.

Task:
<one concrete outcome>

Boundaries:
- <task-specific constraint or authority limit>

Read:
- <exact active backlog, architecture, issue, or runbook link>

Evidence:
- <durable path and what it proves>
- <ephemeral path, clearly labeled, only when material>
```

After this core, add a short `Repository note`, `Validation`, `Fresh live
state`, or `Unrecorded follow-up` paragraph only when its condition above is
met. Do not emit empty headings, placeholder values, or `none` sections.
