# SQLite Contract

## Core

Use `install_cell_core()` and `validate_cell_core()`. Install no optional generic
profiles.

## Authority tables

| Table | Primary key | Exact evidence |
|---|---|---|
| `asset_editions` | `edition_id` | manifest and source-verification JSON |
| `asset_occurrences` | one-based JSONL `source_ordinal` | exact structures line |
| `asset_schemas` | `schema_signature` | exact schemas line |
| `asset_schema_families` | `schema_signature` | exact families line, including membership array |
| `asset_failures` | one-based JSONL `source_ordinal` | exact failures line |

`occurrence_id` is never a key or foreign key. It is a non-unique indexed producer
field. Families are not expanded; use `json_each` for ad hoc membership queries.

Schemas store only exact `source_json`; nested schema JSON is obtained with
`json_extract`. Failures do not duplicate occurrence fields beyond the useful typed
diagnostic columns.

## Audited SQLite affinities

The complete E2 streams establish these mappings:

- `source_ordinal`, manifest totals, `embedded_depth`, sizes, and mtimes use `INTEGER`.
  All observed JSON numbers mapped to these columns are integral; reject booleans and
  fractional values rather than coercing them.
- Hashes, producer IDs, paths/names, content type, route, status, schema kind, and exact
  JSON use `TEXT`.
- Occurrence `resource_name` is nullable (681 of 45,101 rows are null).
- Occurrence `tika_error` is nullable (14 strings); failure `resource_name` and
  `tika_error` are likewise nullable.
- Every observed occurrence ID, content hash, and schema signature is a 64-character
  string. Validate shape, but do not promote occurrence ID to identity.
- All `source_json` columns are `TEXT NOT NULL CHECK(json_valid(source_json))`.

No nested schema leaf is promoted into a typed SQLite column in v1. Its original JSON
number/string/boolean/null type remains authoritative inside `source_json`.

## Indexes

Create only indexes for occurrence ID, content hash, source capture, route, status,
schema signature, embedded depth, and family signature. Do not add FTS or speculative
derived indexes.

## Forbidden relations

The finished cell must not contain provider-owned parse-task/artifact tables,
`_raw_sources`, `_raw_chunks`, `_edges_tree`, `_edges_reference`, generic chunks,
retrieval FTS, or vector relations.

## Metadata

`_metadata` records provider/format versions, static lifecycle, edition/capture paths,
manifest and source-verification hashes, transformation hash, relation counts, source
authority, recovery rule, and explicit limitations.
