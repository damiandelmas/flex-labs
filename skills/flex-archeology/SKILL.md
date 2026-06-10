<!-- shared from the flex skill library — expects flex >= 0.31 with claude_code/codex cells -->
---
name: flex:archeology
description: Reconstruct project history from Flex evidence. Use when the user wants to recover workstreams, shifts, session assignments, artifact lineages, file history, or other retroactive knowledge surfaces from AI coding-agent sessions, git/file activity, context docs, and Flex cells.
user-invocable: true
argument-hint: "workflow + target, e.g. 'workstreams for <YOUR_PROJECT>/context into <YOUR_PROJECT>/workstreams-v2'"
---

# flex:archeology

Flex archeology turns historical evidence into durable project memory. The sources are AI coding-agent sessions, git/file activity, context docs, changelogs, plans, commits, and Flex search surfaces. The output is not a speculative plan; it is a reconstructed map of what the project has actually been doing.

## Router

Use the smallest workflow that fits:

- **Workstreams**: read `workstreams/workflow.md` when reconstructing workstreams, shifts, timelines, and session assignments for a project.
- **File history**: read `file-history/workflow.md` when tracing recoverable versions of one file.
- **Artifact lineage**: read `artifact-lineage/workflow.md` when writing changelog-style lineage for one artifact.

If the user says only "archeology" and names a project, default to the workstreams workflow. If they name a specific file, default to file history or artifact lineage depending on whether they want versions or narrative.

## Core Rules

- Prefer evidence over taxonomy.
- Reference durable project docs before applying recipe text.
- Keep the artifact compact and agent-useful.
- Make uncertain assignments visible instead of forcing them.
- A session may belong to multiple workstreams.
- Do not create query files unless the user explicitly asks for reusable query artifacts.
