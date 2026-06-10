# Reconstruct Artifact Lineage

`$ARGUMENTS` is the file path to trace.

Given an artifact, reconstruct its full changelog lineage across every session that ever touched it. The output is a set of docpac changelogs that capture decisions, context, and reasoning — not diffs. Future agents read these to understand why the artifact is the way it is.

---

## Step 1 — Find All Iterations

An artifact may have lived at multiple paths over its lifetime — renames, directory restructures, version folders. Use flex to discover every path this file has ever existed at. Query `target_file` with LIKE patterns derived from the filename, and also glob the filesystem for sibling versions (e.g. `version-1/`, `version-2/`, `_iterations/`).

```sql
SELECT DISTINCT target_file, COUNT(*) as ops,
  MIN(created_at) as first_touch, MAX(created_at) as last_touch
FROM messages
WHERE target_file LIKE '%FILENAME%'
GROUP BY target_file ORDER BY first_touch
```

The goal is a complete map of every path this artifact has ever existed at.

## Step 2 — Find All Sessions With Writes/Edits

For every path iteration found in Step 1, find every session that performed a Write or Edit. Group by session and day — a single session spanning multiple days is multiple work units. Include the position range and operation count to understand density.

```sql
SELECT m.session_id, s.title, s.project, m.tool_name,
  COUNT(*) as ops, MIN(m.position) as first_pos, MAX(m.position) as last_pos,
  MIN(m.created_at) as start, MAX(m.created_at) as end, s.message_count
FROM messages m JOIN sessions s ON m.session_id = s.session_id
WHERE m.target_file LIKE '%FILENAME%'
  AND m.tool_name IN ('Write', 'Edit')
GROUP BY m.session_id, DATE(m.created_at)
ORDER BY start
```

This gives you the raw material for chunking. Small sessions (< 300 msgs) are one chunk. Large sessions need sub-splitting.

## Step 3 — Chunk at Natural Seams

Each chunk becomes one changelog file. Chunks should be coherent units of work — a phase where a set of related decisions drove a set of related changes. Natural seam indicators:

- **Day boundaries** — overnight gaps almost always mark phase transitions
- **Time gaps > 1h** — within a session, a long pause usually means context shifted
- **Edit density shifts** — a sudden 3x jump in edits/hour signals a new work mode (e.g. shifting from deliberation to execution)
- **User prompt topic shifts** — check `type = 'user_prompt'` messages at candidate boundaries

For large sessions, bucket edits by position (500-msg buckets) and look at the time distribution to find gaps and density shifts. Use generous context windows — read 200+ messages around each edit cluster. Token cost is negligible relative to the value of capturing decision context. Overlap adjacent chunks by ~200 messages so no decision falls between cracks.

## Step 4 — Generate Changelogs in Parallel

Spawn bounded subagents for independent chunks when useful. Each subagent reads the context window from flex and writes the changelog files. The subagent is as capable as you — give it the session ID, position range, and output path. It will figure out the rest.

Each subagent should:
1. Read user_prompt messages across the position range to understand the decision arc
2. Read Write/Edit tool_call messages to understand what changed
3. Read surrounding assistant messages for reasoning and alternatives considered
4. Write the changelog

**Output path convention:** `{artifact_dir}/context/changes/code/{YYMMDD}-{HHMM}_{slug}.md`

If a `context/changes/code/` directory already exists with changelogs, read one for format. If not, use this format:

```markdown
---
schema_version: "v6_adaptive"
type: "paper.changelog"
keywords: "relevant keywords for retrieval"
session_id: "SESSION_ID"
---

# Title — version/phase context

## Request
> "quoted user prompts that drove this work"

## Overview
One paragraph summarizing what happened and why.

## Decisions

### Decision title
What was decided, what it replaced, and why. Include the reasoning — this is the most valuable part for future agents.

## Implementation

### What changed (before -> after)
Concrete structural/content changes. Use tables or code blocks for clarity.

## Patterns

### Reusable insight
Any pattern worth knowing for future work on this artifact.
```

The changelog captures WHY, not WHAT. The diff is in git. The changelog is the decision record that makes the diff legible to a future agent who wasn't there.

## Step 5 — Verify

Glob the output directory and confirm all expected changelogs were written. List them chronologically with one-line summaries to the user.

---

## Notes

- Spawn changelog subagents in parallel where possible.
- Be generous with context windows. 200-500 messages per chunk is fine. The cost of missing a decision is higher than the cost of reading extra tokens.
- The subagent writing each changelog has full flex access — it can query for additional context if the initial window isn't enough
- If an existing `context/changes/code/` directory has changelogs, preserve them. Only create new ones for uncovered time ranges.
- Small sessions (< 100 msgs) can be batched — one agent handles 3-4 small chunks
