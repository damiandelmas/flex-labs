# Schema Forest

This cell is a SQL map of a sealed schema-forest capture. The capture folder remains
the evidence authority.

Query the provider tables directly: `asset_editions`, `asset_occurrences`,
`asset_schemas`, `asset_schema_families`, `asset_failures`, and
`asset_native_tables`. Producer
`occurrence_id` values are not unique; use one-based `source_ordinal` for exact rows.
Use `json_each(source_json, '$.occurrence_ids')` only when family membership expansion
is needed.

Each materialized CSV schema has one generated `sf_csv_<signature-prefix>` table.
`asset_native_tables` maps the full schema signature to that table, preserves the
ordered source-header to SQL-column mapping, and records source-versus-SQL row counts
and digests. Generated CSV tables include the original header as row ordinal zero and
retain every subsequent record, blank field, duplicate row, and multiline value.

Only the verified top-level inputs have direct file recovery. Embedded occurrences are
traceable to a source container but are not independently recoverable from this cell.
Raw specialist artifacts are excluded because their receipts are not trusted. This
XLSX cell values are materialized; richer XLSX package structures plus PDF, DOCX, image,
and IES values remain source-only. This cell contains no
product reconciliation, FTS, vectors, or generic chunk or tree surface.
