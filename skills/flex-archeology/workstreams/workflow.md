# Workstreams Archeology

This workflow reconstructs the working shape of a project from historical AI coding-agent sessions, git/file activity, project context docs, and Flex search. It turns messy past work into a stable project-management surface: workstreams, shifts, timelines, and evidence-backed session assignments. The goal is to retroactively recover what the project has actually been doing and make it legible for future agents. Think of it as rebuilding a lightweight Linear/Jira-style project map after the fact, using traces already left behind in sessions, commits, changelogs, docs, and touched files. Rely on reference docs and live evidence, then use judgment to name and organize the work. Keep the output compact, durable, and useful for continuing work.

## Output Shape

Use this v0 package shape:

```text
workstreams/
├── _overview.md
├── _gantt.md
├── _sessions.md
├── workstreams/
│   └── {workstream}.md
└── shifts/
    └── {shift}.md
```

This is the minimum package contract, not a mandate to flatten the project's native structure. If an existing or evidence-derived topology is more specific, preserve it. For example, a project may naturally want:

```text
workstreams/
├── foundation/
├── embedding/
├── modules/
├── release/
├── test/
├── meta/
├── shifts/
├── _gantt.md
├── _overview.md
└── _sessions.md
```

Do not collapse dozens of concrete lanes into a small set of vague PM buckets just to satisfy the compact shape. A high-resolution lane map is better than a polished abstraction when future agents need to route work, retrieve lineage, or assign sessions.

Meanings:

- `_overview.md`: what this package covers, how it was reconstructed, source coverage, caveats.
- `_gantt.md`: timeline view. Workstreams appear as lanes; shifts appear as point-in-time markers.
- `_sessions.md`: assignment ledger mapping historical sessions to one or more workstreams.
- `workstreams/{workstream}.md`: durable ongoing line of work.
- `shifts/{shift}.md`: before/after transition in architecture, strategy, naming, scope, or operating model.

## Step 1 - Load Knowledge

Read these flex skill reference docs when present in your installation:

- `<FLEX_SKILLS_ROOT>/flex-context/SKILL.md`
- `<FLEX_SKILLS_ROOT>/flex/reference/flex-primitives.md`
- `<FLEX_SKILLS_ROOT>/flex/reference/flex-toolkit.md`
- `<FLEX_SKILLS_ROOT>/flex/reference/flex-workflows.md`

Also read project context docs when available:

- `<YOUR_PROJECT>/context/about.md`
- `<YOUR_PROJECT>/context/map.md`

Treat these as reference material, not rigid law.

## Step 2 - Load Project Memory

Use flex search and direct file reads to sample project memory:

- `context/current`
- `context/changes/code`
- `context/changes/design`
- `context/changes/workflow`
- `context/changes/states`
- `context/plans`
- `context/roadmap`
- `context/vision`

Prefer project-authored context over stale recipe text. If context folders differ, adapt to the project.

## Step 3 - Discover Candidate Shape

Use independent evidence signals:

- changelog stems
- design docs
- active and completed plans
- current architecture docs
- source/module topology
- Flex session communities and hubs
- user prompts and recent decisions
- files often touched together
- git history when available

Do not trust any single signal. Do not use `community_label` as the taxonomy by itself.

## Step 4 - Define Workstreams And Shifts

A **workstream** is a durable ongoing line of work with a coherent concern, history, and body of evidence.

A **shift** is a point-in-time before/after change in architecture, strategy, naming, scope, or operating model.

Keep the taxonomy as simple as the project allows, but no simpler. Do not invent initiatives, sprints, or milestones in v0 unless the user asks for them.

Taxonomy fidelity rules:

- Preserve concrete project-native lanes when they already exist or clearly emerge from evidence.
- Prefer inspectable lanes like `foundation/compile`, `modules/claude_code`, `release/install`, or `embedding/type-resolve` over abstract rollups like "Cell Substrate and SDK" when the project has enough evidence to support the finer shape.
- Use rollups only as section headings or summaries; do not let them replace concrete lanes.
- If a previous workstream package exists and appears more useful than the new reconstruction, treat it as primary evidence. Improve it with `_sessions.md` and better lineage instead of replacing it with a flatter taxonomy.
- "Compact" means concise files and clear navigation. It does not mean fewer lanes.
- A good workstream package should let an agent choose the right lane for a new task without first decoding a broad category.

## Step 5 - Assign Sessions

Write `_sessions.md` before synthesizing workstream narratives. This is the traceable bridge between taxonomy and lineage.

Rules:

- A session may belong to multiple workstreams.
- Assignment must be evidence-backed.
- Prefer user prompts, files touched, commits/changelogs, final summaries, and file archaeology over generic similarity.
- Do not force every session into a workstream.
- Keep ambiguous and excluded sessions visible when useful.

Suggested ledger:

```markdown
# Session Assignments

## Summary

- Total sessions considered:
- Sessions assigned:
- Workstreams:
- Ambiguous sessions:
- Excluded sessions:

## Assignments

| Session | Date | Workstreams | Evidence | Confidence |
|---|---|---|---|---|

## Ambiguous

| Session | Possible Workstreams | Why Ambiguous | Next Check |
|---|---|---|---|

## Excluded

| Session | Reason |
|---|---|
```

## Step 6 - Write Workstreams And Shifts

For each workstream:

```markdown
# {Name}

## Summary
3-7 sentences.

## Evidence
- sessions
- files/docs/changelogs/commits
- relevant Flex searches or presets used

## Timeline
Key moments.

## Current State
What seems true now.

## Open Questions
Evidence-backed uncertainty only.
```

For each shift:

```markdown
# {Name}

## Before

## After

## When

## Why It Mattered

## Evidence
```

## Step 7 - Verify And Revise

Check:

- `_overview.md`, `_gantt.md`, and `_sessions.md` exist.
- Every workstream has a clear concern and evidence.
- Every shift is a transition, not an ongoing lane.
- Session assignments are auditable.
- Obvious sibling-workstream bleed is documented or corrected.
- Uncertainty is visible.

If verification changes the taxonomy, revise `_sessions.md` first, then update narratives.

## Long-Run Parallel Execution

For large projects, distribute this workflow across multiple coding-agent sessions rather than one terminal context.

Use generic roles first:

```text
archeology-orchestrator
archeology-worker-1
archeology-worker-2
archeology-worker-3
archeology-verifier
```

Do not name worker slices before evidence discovery. The orchestrator decides worker ownership after the initial taxonomy and `_sessions.md` seed exist. Worker slices may emerge as subsystems, eras, workstream clusters, high-activity session groups, or shift groups.

### Worker Contract

Each worker must be given:

```text
FLEET={task-specific fleet name, e.g. flex-archeology}
PROJECT_CWD={project repo root or requested working directory}
OUTPUT_ROOT={absolute path to the workstreams package being written}
WORKER_NAME=archeology-worker-{n}
```

The worker prompt must include:

```text
You are {WORKER_NAME}.
Your cwd is {PROJECT_CWD}.
Your only write root is {OUTPUT_ROOT}.
Do not modify sibling baseline workstreams directories.
Read {OUTPUT_ROOT}/_sessions.md and {OUTPUT_ROOT}/_overview.md first.
Own only these files: {explicit file list}.
Use evidence from Flex/session/docs, not invented taxonomy.
When done, append a short status to {OUTPUT_ROOT}/_runlog.md.
```

Recommended sequence:

1. Orchestrator loads references and project memory.
2. Orchestrator drafts candidate taxonomy and initial `_sessions.md`.
3. Orchestrator assigns generic workers to evidence-derived slices.
4. Workers write assigned `workstreams/*.md` and `shifts/*.md` drafts.
5. Verifier audits taxonomy, session assignment, sibling bleed, and missing obvious threads.
6. Orchestrator integrates, revises `_sessions.md`, and finalizes the package.

Keep recursion shallow. Workers should be bounded helpers only — they should not spawn further sub-workers.
