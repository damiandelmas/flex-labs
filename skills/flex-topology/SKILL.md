<!-- shared from the flex skill library — expects flex >= 0.31 with claude_code/codex cells -->
---
name: flex:topology
description: Map the shape of the user's recent work across workstreams. Runs a pre-built topology query, then synthesizes fingerprints + hub openers into a 20-second-scannable standup with workstreams, spillover, and a pulse grid.
user-invocable: true
---

**DO NOT USE ANY TOOLS. NO Read, NO Grep, NO Glob, NO Agent, NO Bash. ONLY synthesize the data below into the output format. The data from the shell commands IS your input. Go straight to output.**

# flex:topology

!`scripts/topology-run.sh 14`

---

DO NOT run any additional queries. All data is above.

## Synthesis Instructions

You are mapping the **shape of someone's work** — not listing data, but revealing structure. This is a seeded topology: the data above was discovered by starting with sessions from the last 14 days, extracting their communities, then expanding each community's full lineage.

**You have three layers of signal — use all of them:**
1. **Structural** — session counts, centrality, hub/bridge flags, temporal pulse
2. **Fingerprints** — terse operation summaries on hubs and bridges (the `fingerprint` field). These tell you WHAT was done.
3. **Hub openers** — the first user prompt from each community's top hub. These tell you WHY it was done.

The fingerprints and openers are the most valuable. They carry intent and substance. Use them to say what actually happened in each workstream, not just that it was "ramping" or "cooling."

**CRITICAL tone rule:** Write like a coworker giving a casual standup, NOT like a technical changelog. No variable names, no function signatures, no hex colors, no CTE counts. The reader wants to scan this in 20 seconds.

**Example of what NOT to write:**
> Sustained high output for three weeks running. The work spans outreach tooling and cross-project integration. Message density is high — these are long working sessions, not quick queries.

That says nothing. It's shape language with zero substance. Here's what the SAME data should produce:

> Stress-tested flex with three parallel agents running increasingly wild queries — found that FTS and semantic search return incompatible ID types, and that one of the search modifiers was being silently ignored. Also traced how the graph system evolved through five different incarnations. This is where new capabilities get discovered by pushing existing ones to their limits.

**The rule:** Every workstream paragraph must contain at least one specific thing that happened, extracted from the hub openers and fingerprints. If a paragraph could describe any project on earth, it's too vague — rewrite it.

**Output format — follow this layout exactly:**

```
**OVERVIEW**

5-7 sentences. Energy weather report. Reference specific work from fingerprints/openers.


**WORKSTREAMS**

**Workstream Name**
repos: my-app, website, infra

3-5 sentence paragraph. Say what was built/decided/shipped — pull from fingerprints and openers.

**Next Workstream**
repos: website, .claude

3-5 sentence paragraph.

(repeat for top 5, then one "Also active:" line)


**SPILLOVER**

2-3 sentences. What concrete work bled between workstreams? Use bridge fingerprints to identify: a bug found in one stream that got fixed in another, a design decision that forced changes elsewhere, a session that started as one thing and ended touching another. If the bridges don't show anything concrete, say "workstreams were mostly independent this period" and move on.


**PULSE**

◼ heat grid with actual dates
```

**Formatting rules:**
- Section headers are **bold all-caps**: `**OVERVIEW**`, `**WORKSTREAMS**`, `**SPILLOVER**`, `**PULSE**`
- Workstream names are **bold**: `**Outreach**`
- No markdown headers (no `#`, `##`, `###`), no `───` lines, no `▸` bullets
- TWO blank lines before each section header, ONE blank line between workstreams

**Section guidance:**

1. **OVERVIEW.** Energy weather report grounded in specifics. Which workstreams are hot, cooling, dormant. What shifted. Reference concrete work (from fingerprints) not abstract shape. Do NOT describe or explain the projects — the user built them.

2. **WORKSTREAMS.** Top 5 by recent activity. Each gets a **bold name** header, a `repos:` line, and a 3-5 sentence paragraph. Lead with what was actually done (fingerprints tell you). Weave in temporal shape as context, not the point. Fold remaining communities into "Also active:" at the end.

3. **SPILLOVER.** What leaked between workstreams. Look at bridge session fingerprints — did a bug found during outreach cause a fix in the engine? Did a design decision in the website force changes in the core? Name the concrete thing that crossed the boundary. If nothing concrete crossed, say so — "workstreams were independent" is a valid and useful answer. Do NOT fabricate connections by restating community labels with filler verbs.

4. **PULSE.** Heat grid using ◼ blocks. Actual dates (e.g. "Feb 10") NOT ISO week codes. Scale ◼ relative to peak. Use · for zero. Example:

```
                    Feb 10  Feb 17  Feb 24  Mar 3   Mar 10
Core Engine         ◼◼      ◼◼◼◼◼   ◼◼◼     ◼◼      ◼
Outreach            ·       ◼       ◼◼      ◼◼◼◼    ◼◼◼◼◼
```

**Style:**
- Prose paragraphs, not bullet lists or stat dumps
- The user built these projects — describe shape of work, not what things are
- Ground every workstream description in fingerprint/opener evidence — no vague "ramping/cooling" without substance
- **Plain language** — translate technical details into human-readable summaries. "Shipped the rename" not "propagated vec_search→vec_ops through MCP server". "Fixed two indexing bugs" not "compute_scores() list/dict mismatch and __FLEX_PYTHON__ template failure"
- Relative scale — "the densest workstream" not "176 sessions, 35K messages"
- No speculation about future plans — structural facts only
- Scannable in 20 seconds — if a sentence requires domain expertise to parse, rewrite it
- Under 600 words total (excluding pulse grid)

$ARGUMENTS
