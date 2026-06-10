---
name: flex:digest
description: Workstream-oriented standup of recent coding-agent activity. Runs focused SQL over a sessions cell (inventory, file hotspots, delegations, open loops), then synthesizes the combined payload into a coworker-style digest.
user-invocable: true
argument-hint: "optional cell + window, e.g. 'claude_code today', 'codex 3d'"
---

# flex:digest

The programmatic version of `@digest`: instead of one preset result, run four focused queries and synthesize the combined payload. Works against any coding-agent sessions cell (`claude_code`, `codex`, ...).

## Data Flow

```text
queries/digest/*.sql
  -> scripts/flex-query.sh <cell>
  -> model synthesis
```

## Execution

From this skill directory (default cell `claude_code`; substitute any sessions cell):

```bash
cat queries/digest/inventory.sql   | scripts/flex-query.sh claude_code
cat queries/digest/files.sql       | scripts/flex-query.sh claude_code
cat queries/digest/delegations.sql | scripts/flex-query.sh claude_code
cat queries/digest/open-loops.sql  | scripts/flex-query.sh claude_code
```

The four phases:

1. **Inventory** — recent non-agent sessions: community labels, centrality, hub/bridge flags, fingerprints, project counts.
2. **Files** — last-day Read/Edit/Write hotspots, grouped by file identity when available.
3. **Delegations** — parent and child agent relationships.
4. **Open loops** — overnight sessions and large sessions that may need pickup.

Extra semantic assets (not part of the standard run, useful for deeper passes): `user-voice.sql`, `user-voice-tight.sql`, `secondary-work.sql`, `workstream-clusters.sql`.

## Synthesis Instructions

After the four payloads are in, DO NOT run additional queries. Synthesize:

- **Write like a coworker giving a casual standup**, not a changelog. The reader scans in 20 seconds.
- Group by workstream (community labels + fingerprints carry the substance — use them to say what actually happened, not that work "continued").
- Surface file hotspots only when they sharpen a workstream's story.
- End with **open loops**: what looks unfinished, what may need pickup today, and any delegation chains left hanging.
- Preserve uncertainty instead of forcing every session into a taxonomy.

## Rules

- Start from evidence, not vibes.
- Keep SQL in `queries/`, mechanics in `scripts/`, synthesis rules here.
- Treat scores/centrality as ordering signals within one digest, never as absolute values.
