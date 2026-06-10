# Lobsters Cell Instructions

This cell indexes public Lobsters tech-news stories and comments pulled through
the unauthenticated JSON endpoints — no credentials required. It installs via
flex-labs. Each source is one story thread; chunks are story bodies and
flattened comments.

Start here:

```text
cell="lobsters" query="@orient"
```

`@orient` returns the live schema, views, presets, graph entry points, and
these instructions. Always call it once before other queries.

## What This Cell Is For

Use the lobsters cell when the question involves:

- Lobsters story discussions, community opinions, and link commentary
- semantic search over systems programming, language design, security, and
  technical topics that dominate the Lobsters corpus
- tag-scoped retrieval: stories tagged `rust`, `sqlite`, `networking`, etc.
- comment thread navigation: depth, parent chains, per-story replies
- top-story discovery sorted by score
- graph-assisted browsing: hub stories, cross-community connectors

Cell scope is bounded by `--tags`, `--pages`, and `--since` at build time.
Check `_meta` for `tags` and `pages` before diagnosing missing results.

## First Move

```text
cell="lobsters" query="@orient"
```

Every Flex query must be SQL or a preset. Wrap conceptual searches in
`vec_ops()`. Wrap exact terms in `keyword()`. Plain text is not a query.

## Core Surfaces

`chunks` is the unified retrieval surface. Every story body and comment is a
row here. Key columns: `id`, `content`, `created_at`, `type`
(`story`|`comment`), `source_id`, `position`, `title`, `author`, `score`,
`thread_url`, `story_url`, `thread_score`, `thread_comments`, `thread_tags`,
`depth`, `parent_id`, `centrality`, `is_hub`, `is_bridge`, `community_id`.

`threads` is the source-level surface — one row per story. Columns:
`source_id`, `title`, `author`, `score`, `num_comments`, `url`, `story_url`,
`tags`, `file_date`, `chunk_count`, `centrality`, `is_hub`, `is_bridge`,
`community_id`. Use it for story-level ranking and structural surveys.

`thread_tags` on `chunks` holds story-level tags as a JSON array. Use
`json_each(thread_tags)` for exact matching or `thread_tags LIKE '%rust%'`
as a quick fallback.

## Choosing Search Mode

**Structural** (no embeddings) — use first when you know a tag, score range,
or date. Free.

```sql
SELECT title, author, score, num_comments, story_url, thread_url
FROM threads
ORDER BY score DESC
LIMIT 20;
```

**Keyword** — exact terms, project names, quoted phrases. Scope to `story` to
prevent comment noise:

```sql
SELECT k.id, k.rank, k.snippet, c.title, c.author, c.type
FROM keyword('"structured concurrency"', 'SELECT id FROM chunks WHERE type = ''story''') k
JOIN chunks c ON k.id = c.id
ORDER BY k.rank DESC
LIMIT 15;
```

**Semantic** — conceptual or cross-vocabulary search. Put all constraints in
the pre-filter; post-`WHERE` on sparse conditions starves the pool.

```sql
SELECT v.id, v.score, c.title, c.author, c.type,
       substr(c.content, 1, 400) AS preview
FROM vec_ops(
  'similar:developer experiences with sqlite in production diverse',
  'SELECT id FROM chunks WHERE type = ''story'''
) v
JOIN chunks c ON v.id = c.id
ORDER BY v.score DESC
LIMIT 15;
```

Find varied community opinions — suppress a dominant subtopic:

```sql
SELECT v.id, v.score, c.title, c.author, c.depth,
       substr(c.content, 1, 400) AS preview
FROM vec_ops(
  'similar:tradeoffs of async runtime design in systems languages diverse suppress:tokio',
  'SELECT id FROM chunks WHERE type = ''comment'''
) v
JOIN chunks c ON v.id = c.id
ORDER BY v.score DESC LIMIT 12;
```

**Hybrid** — keyword anchors the pool; semantic scores within it:

```sql
SELECT v.id, v.score, k.rank, c.title, c.author,
       substr(c.content, 1, 300) AS preview
FROM keyword('"memory safety"') k
JOIN vec_ops('similar:language design choices for safe systems programming') v ON k.id = v.id
JOIN chunks c ON k.id = c.id
ORDER BY v.score DESC LIMIT 10;
```

## Module Sections

**Stories by tag** (structural):

```sql
SELECT title, author, score, num_comments, story_url
FROM threads
WHERE EXISTS (
    SELECT 1 FROM json_each(tags) j WHERE j.value = 'rust'
)
ORDER BY score DESC LIMIT 20;
```

**Comment thread for a story** (navigate by `source_id` and `depth`):

```sql
SELECT position, depth, author, score, parent_id,
       substr(content, 1, 500) AS body
FROM chunks
WHERE source_id = 'lob_<short_id>' AND type = 'comment'
ORDER BY position;
```

**Hub stories** (highest centrality):

```sql
SELECT v.id, v.score, c.title, c.author, c.centrality, c.community_id
FROM vec_ops('similar:language runtime and performance engineering') v
JOIN chunks c ON v.id = c.id
WHERE c.is_hub = 1
ORDER BY c.centrality DESC LIMIT 10;
```

## Preset Bias

Prefer presets when they fit:

- `@orient` — live schema, views, presets, graph summary, samples
- `@recent days=7` — latest stories and comments by timestamp
- `@top days=30` — highest-score stories and comments
- `@tag tag=rust` — chunks from threads tagged with a Lobsters tag
- `@bridges` — cross-community connector stories
- `@genealogy concept=<term>` — concept lineage through hubs and timeline
- `@health` — pipeline health: embedding coverage, graph freshness, op log

Use raw SQL when the question is structural, when a preset is too broad, or
when you need a precise pre-filter before semantic scoring.

## Reporting Results

Include with every result set:

- cell name: `lobsters`
- chunk `id` and `source_id`
- `created_at`, `type` (`story` or `comment`), `title`, `author`
- `thread_url` and `story_url` for direct links
- `depth` and `parent_id` for comment navigation
- vector score or keyword rank when `vec_ops`/`keyword` was used
- a compact excerpt unless full body was requested

When the scope does not cover a tag or date range, say so and cite `_meta`.
