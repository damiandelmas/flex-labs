# Digest Workflow

The digest workflow summarizes recent `claude_code` activity into a workstream-oriented standup. It is the programmatic version of `@digest`: instead of one preset result, it runs several focused SQL queries and asks the model to synthesize the combined payload.

## Data Flow

```text
queries/digest/*.sql
  -> scripts/flex-query.sh claude_code
  -> model synthesis
```

The current installed Claude skill runs four phases:

1. **Inventory**: `queries/digest/inventory.sql`
   Recent non-agent sessions, community labels, centrality, hub/bridge flags, fingerprints, and project counts.
2. **Files**: `queries/digest/files.sql`
   Last-day Read/Edit/Write hotspots, grouped by SOMA file UUID when available.
3. **Delegations**: `queries/digest/delegations.sql`
   Parent and child agent relationships from `_edges_delegations`.
4. **Open loops**: `queries/digest/open-loops.sql`
   Overnight sessions and large sessions that may need pickup.

Older semantic query assets are retained but not used by the current installed skill:

- `queries/digest/user-voice.sql`
- `queries/digest/user-voice-tight.sql`
- `queries/digest/secondary-work.sql`
- `queries/digest/workstream-clusters.sql`

## Execution

From the `skills/flex-programmatic` directory:

```bash
cat queries/digest/inventory.sql | scripts/flex-query.sh claude_code
cat queries/digest/files.sql | scripts/flex-query.sh claude_code
cat queries/digest/delegations.sql | scripts/flex-query.sh claude_code
cat queries/digest/open-loops.sql | scripts/flex-query.sh claude_code
```

`flex-query.sh` tries the warm MCP HTTP endpoint first, then falls back to `flex search`.

## Synthesis Contract

Think in workstreams, not sessions. A workstream is a cluster of sessions, files, and agents pursuing the same goal.

Build the answer in this order:

1. Workstreams: group sessions by `community_label`, shared files, hub/bridge evidence, and fingerprints.
2. Work rhythm: identify dense periods, overnight carries, and long-running sessions.
3. Open loops: call out overnight sessions, sessions over 100 messages, or delegated work that may not have been consumed.
4. File heat: top files touched across multiple sessions.

Style:

- Lead with workstreams, not chronology.
- Use 2-3 sentences per major workstream.
- Use one line per minor session.
- Do not speculate about future plans. Only describe what the data shows.

## Testing Status

Stock `@digest` is covered by Docker/E2E/headless tests. This chained-query workflow has live usage evidence but no focused smoke test yet.

Before release, add a test that runs the four active SQL files through `flex-query.sh` against a seeded `claude_code` cell and asserts valid JSON/text output.
