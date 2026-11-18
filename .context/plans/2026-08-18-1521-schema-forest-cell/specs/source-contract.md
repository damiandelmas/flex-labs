# Source Contract

## Required identities

`schema-forest.v1` binds four compressed JSONL files, the schema tool lock, and the Tika
manifest by SHA-256. The source-verification receipt binds five top-level source files
by root-relative path, SHA-256, and size. Mtime is evidence, not identity.

`.receipts/schema-build.json`, when present, must exactly mirror
`.schemas/manifest.json`; it is not an independent authority.

## Row identity

- Structures: line ordinal is unique; `occurrence_id` is non-unique evidence.
- Schemas: `schema_signature` is unique.
- Families: `schema_signature` is unique; `occurrence_ids` is an ordered multiset.
- Failures: line ordinal is unique; `occurrence_id` is non-unique evidence.

Known fields are indexed while the exact source line is retained. Unknown manifest
versions fail closed.

The source files are LF-terminated JSONL with no CRLF records. `source_json` stores the
exact UTF-8 bytes between record delimiters, excluding the single LF terminator. The
loader must not reserialize a parsed object. Current maximum record sizes are 4,376
bytes (structures/failures), 397,483 bytes (schemas), and 1,961,772 bytes (families), so
stream by record without imposing a smaller arbitrary line limit.

## Status and recovery

Route and status values remain verbatim. For example, `nested` is a route while
`covered_by_parent` is a status; these distinctions are not collapsed.

Only the five source-verification entries have direct source paths. Embedded rows carry
container/source name, resource name, content hash, route, and depth, making them
queryable and traceable to a verified source container. The v1 compiler must not claim
independent recovery that the structures stream does not provide.

## Excluded raw seam

`.schemas/raw` is not a v1 input. None of its 1,221 individual receipts is
manifest-hash-bound; one `tika_only` no-op receipt exists beyond the declared 1,220
executed specialist tasks, and declared hash/size mismatches remain. Its files stay on
disk but are not presented as trusted SQLite evidence until the producer seam is
repaired and resealed.
