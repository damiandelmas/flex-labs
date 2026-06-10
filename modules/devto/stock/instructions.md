# Dev.to Cell Instructions

This cell indexes public Dev.to articles and comments pulled through the Forem
API — no credentials required. It installs via flex-labs. Each source is one
article; each chunk is an article body or a flattened comment.

The configured scope is the durable contract. Read it first:

```text
cell="devto" query="@orient"
```

`@orient` returns the live schema, views, presets, graph entry points, and
these instructions. Always call it once before other queries.

## What This Cell Is For

Use the devto cell when the question involves:

- article bodies and comment discussions on Dev.to topics
- semantic search over developer experience, tutorials, opinions, and ecosystem news
- author-scoped retrieval (via `--authors` scope or `@me`)
- tag-scoped browsing: which articles exist under a given Forem tag
- graph-assisted discovery: hub articles and cross-community connectors
- quantitative questions: article counts by tag, score distributions, reading time

Cell scope is bounded by `--tags`, `--authors`, `--since`, and `--limit` at
build time. Check `@scope` before diagnosing missing results.

## First Move

```text
cell="devto" query="@orient"
```

Every Flex query must be SQL or a preset. Wrap conceptual searches in
`vec_ops()`. Wrap exact terms in `keyword()`. Plain text is not a query.

## Core Surfaces

`chunks` is the unified retrieval surface. Every article body and comment is a
row here. Key columns: `id`, `content`, `created_at`, `type`
(`article`|`comment`), `title`, `author`, `score`, `article_url`,
`article_score`, `article_comments`, `tags`, `reading_time`, `centrality`,
`is_hub`, `is_bridge`, `community_id`.

`tags` stores a JSON array. Presets use `json_each(tags)` for exact tag
matching; `tags LIKE '%python%'` works as a quick fallback.

## Choosing Search Mode

**Structural** (no embeddings) — use first when you know a tag, author, date
range, or type. Free.

```sql
SELECT title, author, article_score, article_comments, reading_time, article_url
FROM chunks
WHERE type = 'article'
  AND tags LIKE '%sqlite%'
ORDER BY article_score DESC
LIMIT 20;
```

**Keyword** — exact terms, library names, quoted phrases. Scope to `article`
to prevent comment noise:

```sql
SELECT k.id, k.rank, k.snippet, c.title, c.author, c.type
FROM keyword('"vector database"', 'SELECT id FROM chunks WHERE type = ''article''') k
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
  'SELECT id FROM chunks WHERE type = ''article'''
) v
JOIN chunks c ON v.id = c.id
ORDER BY v.score DESC
LIMIT 15;
```

Suppress the dominant theme to surface edges:

```sql
SELECT v.id, v.score, c.title, substr(c.content, 1, 400) AS preview
FROM vec_ops(
  'similar:LLM agent memory and context management diverse suppress:fine-tuning',
  'SELECT id FROM chunks WHERE created_at >= date(''now'', ''-30 days'')'
) v
JOIN chunks c ON v.id = c.id
ORDER BY v.score DESC LIMIT 12;
```

**Hybrid** — keyword anchors the pool; semantic scores within it:

```sql
SELECT v.id, v.score, k.rank, c.title, c.author,
       substr(c.content, 1, 300) AS preview
FROM keyword('"MCP server"') k
JOIN vec_ops('similar:building and securing MCP tool servers') v ON k.id = v.id
JOIN chunks c ON k.id = c.id
ORDER BY v.score DESC LIMIT 10;
```

## Module Sections

**Per-tag article counts**:

```sql
WITH t AS (
  SELECT j.value AS tag, c.id
  FROM chunks c, json_each(c.tags) j
  WHERE c.type = 'article'
)
SELECT tag, COUNT(DISTINCT id) AS articles
FROM t GROUP BY tag ORDER BY articles DESC LIMIT 30;
```

**Comment thread for a specific article** (`source_id` from `chunks`):

```sql
SELECT position, author, score, substr(content, 1, 500) AS body
FROM chunks
WHERE source_id = 'devto_<id>' AND type = 'comment'
ORDER BY position;
```

**Hub articles** (highest centrality):

```sql
SELECT v.id, v.score, c.title, c.author, c.centrality, c.community_id
FROM vec_ops('similar:AI tooling and developer workflow automation') v
JOIN chunks c ON v.id = c.id
WHERE c.is_hub = 1
ORDER BY c.centrality DESC LIMIT 10;
```

## Preset Bias

Prefer presets when they fit:

- `@orient` — live schema, presets, graph summary, samples
- `@scope` — configured tags, authors, recency window, comment settings
- `@me days=30` — articles and comments by configured authors
- `@recent limit=50` — latest chunks by timestamp
- `@by-tag tag=python` — chunks matching a tag
- `@tags` — count indexed articles per tag
- `@bridges` — cross-community connector articles
- `@genealogy concept=<term>` — concept lineage through hubs and timeline
- `@health` — pipeline health: embedding coverage, graph freshness, op log

Use raw SQL when the question is structural, when a preset is too broad, or
when you need a precise pre-filter before semantic scoring.

## Reporting Results

Include with every result set:

- cell name: `devto`
- chunk `id` and `source_id`
- `created_at`, `type` (`article` or `comment`), `title`, `author`
- `article_url` for direct links
- vector score or keyword rank when `vec_ops`/`keyword` was used
- a compact excerpt unless full body was requested

When the scope does not cover a tag or date range, say so and cite `@scope`.
