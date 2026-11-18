# Schema Forest Native Flex Cell

## Outcome

Compile the sealed schema forest at
`/home/axp/projects/flexchat/sales/clients/e2-lighting/2026-08-17-1423-assets`
into one small, provider-native SQLite Flex cell without walking or parsing the source
assets again.

The asset folder remains the evidence authority. SQLite is only the trustworthy SQL map
of the four manifest-hashed JSONL streams. Document extraction, raw parser outputs,
catalogue reconciliation, FTS, and vectors remain outside v1.

## Decision

Create an external Flex module named `schema_forest` beside `xologic`. Reuse XOLogic's
small staged-build pattern without creating a shared framework:

```text
sealed schema-forest manifest
  -> verify declared inputs and counts
  -> stream four JSONL.zst files into staged native SQLite
  -> install canonical Flex core + five provider tables + @orient
  -> verify logical content and recovery evidence
  -> atomically publish DB and build receipt inside the capture folder
  -> register as a static external cell
```

## Audit corrections incorporated

The audit found facts the first plan got wrong:

- `occurrence_id` is not unique: 45,101 rows contain 10,418 distinct IDs. JSONL line
  ordinal is therefore the occurrence key.
- The 827 family rows preserve a multiset of 45,101 memberships. Families remain one
  exact row each; they are not expanded into a duplicate membership table.
- The 21 failure rows contain only 20 distinct occurrence IDs. Failure line ordinal is
  the key and no ambiguous occurrence foreign key is declared.
- The raw tree contains 1,221 receipts: one extra `tika_only` no-op receipt beyond the
  manifest's 1,220 executed specialist tasks. None of those individual receipts is
  manifest-hash-bound, and 11 declared raw hashes plus 9 sizes currently disagree with
  disk. Raw tasks/artifacts are excluded from v1 rather than being represented as
  trustworthy.
- Depth zero means the five top-level input files, not 3,935 physical occurrences. The
  manifest's physical count is reproduced by `route <> 'nested' AND status <>
  'container'`.
- `.receipts/schema-build.json` is byte-identical to `.schemas/manifest.json`; it is an
  optional receipt mirror, not a second authority.

## Authorities

| Concern | Authority | SQLite treatment |
|---|---|---|
| Original bytes | Existing asset folder and five verified top-level inputs | Capture-relative path/hash evidence only |
| Occurrences | `.schemas/structures.jsonl.zst` | Exact line plus useful indexed fields |
| Schemas | `.schemas/schemas.jsonl.zst` | Exact line plus signature/kind |
| Families | `.schemas/families.jsonl.zst` | One exact row per signature |
| Failures | `.schemas/failures.jsonl.zst` | One exact row per source line |
| Capture identity | `.schemas/manifest.json` | Exact JSON and SHA-256 on the edition |
| Source verification | `.receipts/schema-source-verification.json` | Exact JSON and reverified five source files |
| Build identity | Transformation digest and SQLite SHA-256 | Atomic external receipt |
| Session lineage | Flex Sessions/Ledger | Not copied into this cell |

## Required input contract

Support only `manifest_version = schema-forest.v1` and verify:

- `.schemas/manifest.json`
- `.schemas/structures.jsonl.zst`
- `.schemas/schemas.jsonl.zst`
- `.schemas/families.jsonl.zst`
- `.schemas/failures.jsonl.zst`
- `.receipts/schema-source-verification.json`
- `.vendor/schema-tools.lock.json`
- `.tika/manifest.json`

The four compressed JSONL hashes and byte sizes must match the manifest. The tool-lock
and Tika-manifest hashes must match their declared identities. The five files recorded
by the source-verification receipt are rehashed once during this static build. If
`.receipts/schema-build.json` exists, verify that it exactly mirrors the manifest.

Unknown versions, missing inputs, bad JSON, count/hash disagreement, duplicate schema
signatures, dangling schema references, invalid family membership, or source-byte
tampering fail before publication and preserve the last-good DB.

## Native SQLite contract

Install `install_cell_core()` only; do not install generic source, chunk, tree,
retrieval, or vector profiles.

### `asset_editions`

One row. Stable `edition_id` derives from manifest and verification-receipt hashes. Store
capture root, exact manifest and verification JSON, their paths/hashes, manifest
version, declared totals, tool-lock hash, Tika-manifest hash, and transformation digest.

### `asset_occurrences`

One row for every structures line. One-based JSONL line number `source_ordinal` is the
primary key.
`occurrence_id` remains a non-unique indexed producer field. Also index content SHA-256,
source capture, resource name, content type, embedded depth, route, status, schema
signature, and Tika error. `source_json` retains the exact UTF-8 line.

For every JSONL table, `source_json` is the exact UTF-8 record bytes after removing the
single LF record terminator. No other whitespace or key ordering is changed. This rule
is part of the transformation digest and synthetic tests.

### `asset_schemas`

One row per schemas line, keyed by unique `schema_signature`. Store kind and exact
`source_json`. Do not duplicate the nested schema in a second column; query it with
`json_extract(source_json, '$.schema')`.

### `asset_schema_families`

One row per families line, keyed by schema signature, with exact `source_json`. Query
members using SQLite `json_each(source_json, '$.occurrence_ids')` only when needed.
Compilation validates the complete membership bag, including multiplicity, against the
occurrences stream.

### `asset_failures`

One row per failures line. One-based JSONL line number `source_ordinal` is the primary key;
`occurrence_id` is non-unique evidence. Store status, route, Tika error, schema
signature, and exact `source_json`. Validate that the failures multiset is contained in
the structures multiset; do not declare an ambiguous occurrence foreign key.

No provider convenience views are installed in v1. Agents query these five tables
directly. Minimal indexes cover exact joins and common filters only.

## Orientation

`@orient` is a content-free provider preset backed by `_metadata`, live counts,
`sqlite_master`, and `pragma_table_info`. It reports:

- identity: provider, edition, capture root, manifest hash;
- capabilities: exact edition/occurrence/schema/family/failure SQL;
- live shape: five provider table counts, five top-level files, and the manifest's
  3,935 physical-occurrence predicate;
- recovery: direct recovery of the five verified inputs; embedded rows are queryable and
  traceable to a verified source container but are not independently recoverable from
  the structures stream;
- limitations: raw specialist bundles are currently untrusted and absent; no document
  transcription, product facts, FTS, vectors, or generic chunks/tree.

## Build and publication

1. Resolve and verify all required paths beneath the capture root.
2. Stream each JSONL file with the existing local `zstd` executable; never load a whole
   stream into memory.
3. Build in a temporary sibling directory under `<capture>/.derived/`.
4. Install the canonical core and five provider tables, then insert in source order in
   one transaction.
5. Run `validate_cell_core()`, `PRAGMA integrity_check`, `PRAGMA foreign_key_check`,
   exact count/bag checks, recovery checks, forbidden-table checks, and `@orient`.
6. Compute ordered source-row digests and counts for the candidate. Prove double-build
   logical determinism on the tiny synthetic fixture; do not rebuild and rehash the full
   E2 capture twice. SQLite byte identity is not a contract.
7. Atomically replace `<capture>/.derived/<name>.db`.
8. Atomically write
   `<capture>/.receipts/<name>-schema-forest-native-flex-build.json` with source,
   transform, output, count, and orientation hashes.
9. Register the DB using `lifecycle='static'` and `allow_external=True`.

There is no scheduled refresh or watcher. A new sealed edition is compiled explicitly.

## Commands

Install the external module once:

```bash
flex module install \
  /home/axp/projects/flex/labs/repo/modules/schema_forest \
  --editable
```

Then build and register:

```bash
flex init \
  --module schema-forest \
  --capture /home/axp/projects/flexchat/sales/clients/e2-lighting/2026-08-17-1423-assets \
  --name e2-asset-world
```

## Acceptance

### Synthetic fixture

A runtime-created tiny `schema-forest.v1` fixture proves:

- exact source-line preservation and typed indexes;
- non-unique occurrence/failure IDs remain distinct by line ordinal;
- family membership multiplicity is preserved;
- malformed JSON, bad hashes/counts, dangling signatures/memberships, and path escape
  fail closed;
- failed rebuild preserves last-good bytes;
- `validate_cell_core`, integrity/FK checks, forbidden-table absence, and introspective
  `@orient` pass;
- static external registration uses `allow_external=True`.

Do not add Meta composition, refresh, content extraction, raw-artifact, or convenience
view tests.

### Real E2 capture

The real build must prove:

- 45,101 occurrence rows;
- 827 schema rows;
- 827 family rows;
- 21 failure rows;
- 10,418 distinct producer occurrence IDs remain non-unique evidence;
- family membership bag equals the structures `(schema_signature, occurrence_id)` bag;
- `route <> 'nested' AND status <> 'container'` yields 3,935;
- depth zero yields the five source-verification inputs;
- all five source files, four JSONL streams, tool lock, and Tika manifest match their
  recorded hashes;
- representative CSV, archive, XLSX, PDF, DOCX, image, and IES rows remain queryable by
  their exact occurrence evidence;
- raw parser receipts are explicitly absent from the trusted SQL surface.

Record the command, elapsed time, database bytes, cell hash, source identities, and
counts in the external build receipt. Do not impose an arbitrary database-size or
peak-memory policy before observing the minimal build.

## Non-goals

- No source walking or parser execution.
- No raw specialist artifact ingestion until that capture seam is repaired.
- No artifact content inlining.
- No convenience views, FTS, vectors, chunks, edges, or generic Filesystem tables.
- No PDF/OCR/document normalization.
- No product ontology, catalogue reconciliation, or WooCommerce facts.
- No refresh daemon, watcher, incremental compiler, or shared compiler framework.

## Planned files

- [modules/schema_forest/__init__.py](slots/modules/schema_forest/__init__.py.md)
- [modules/schema_forest/install.py](slots/modules/schema_forest/install.py.md)
- [modules/schema_forest/compile/__init__.py](slots/modules/schema_forest/compile/__init__.py.md)
- [modules/schema_forest/compile/worker.py](slots/modules/schema_forest/compile/worker.py.md)
- [modules/schema_forest/stock/presets/orient.sql](slots/modules/schema_forest/stock/presets/orient.sql.md)
- [modules/schema_forest/stock/instructions.md](slots/modules/schema_forest/stock/instructions.md.md)
- [modules/schema_forest/tests/test_worker.py](slots/modules/schema_forest/tests/test_worker.py.md)
