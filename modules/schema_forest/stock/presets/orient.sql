WITH shape AS (
  SELECT 'asset_editions' AS relation, count(*) AS rows FROM asset_editions
  UNION ALL SELECT 'asset_occurrences', count(*) FROM asset_occurrences
  UNION ALL SELECT 'asset_schemas', count(*) FROM asset_schemas
  UNION ALL SELECT 'asset_schema_families', count(*) FROM asset_schema_families
  UNION ALL SELECT 'asset_failures', count(*) FROM asset_failures
  UNION ALL SELECT 'asset_native_tables', count(*) FROM asset_native_tables
), surface AS (
  SELECT
    m.name AS relation_name,
    m.type AS relation_type,
    p.cid,
    p.name AS column_name,
    p.type AS declared_type,
    p.pk
  FROM sqlite_master AS m
  JOIN pragma_table_info(m.name) AS p
  WHERE m.name LIKE 'asset_%'
    AND m.type IN ('table', 'view')
), native_summary AS (
  SELECT component, count(*) AS tables, sum(sql_rows) AS rows
  FROM asset_native_tables
  GROUP BY component
), edition AS (
  SELECT * FROM asset_editions LIMIT 1
)
SELECT 'identity' AS query, json_object(
  'provider', 'schema_forest',
  'format', (SELECT value FROM _metadata WHERE key = 'format'),
  'lifecycle', 'static',
  'edition_id', (SELECT edition_id FROM edition),
  'capture_root', (SELECT capture_root FROM edition),
  'manifest_sha256', (SELECT manifest_sha256 FROM edition)
) AS results
UNION ALL
SELECT 'capabilities', json_array(
  'exact occurrence rows',
  'exact schema rows',
  'exact family rows with multiplicity',
  'exact failure rows',
  'generated native CSV tables with complete ordered values',
  'live SQLite schema introspection'
)
UNION ALL
SELECT 'shape', (
  SELECT json_group_array(json_object('relation', relation, 'rows', rows))
  FROM shape
)
UNION ALL
SELECT 'source_shape', json_object(
  'verified_top_level_inputs', (
    SELECT count(*) FROM edition, json_each(edition.verification_json, '$.files')
  ),
  'depth_zero_occurrences', (
    SELECT count(*) FROM asset_occurrences WHERE embedded_depth = 0
  ),
  'physical_occurrences', (
    SELECT count(*) FROM asset_occurrences
    WHERE route <> 'nested' AND status <> 'container'
  ),
  'distinct_producer_occurrence_ids', (
    SELECT count(DISTINCT occurrence_id) FROM asset_occurrences
  )
)
UNION ALL
SELECT 'query_surface', (
  SELECT json_object(
    'core_relations', (SELECT json_group_array(json_object(
      'relation', relation_name,
      'type', relation_type,
      'column', column_name,
      'declared_type', declared_type,
      'primary_key', pk
    )) FROM surface),
    'native_tables', (SELECT json_group_array(json_object(
      'component', component,
      'tables', tables,
      'rows', rows
    )) FROM native_summary),
    'detail_query', 'SELECT * FROM asset_native_tables ORDER BY component, schema_signature'
  )
)
UNION ALL
SELECT 'recovery', (
  SELECT json_group_array(json_object(
    'path', j.key,
    'sha256', json_extract(j.value, '$.verified_sha256'),
    'bytes', json_extract(j.value, '$.size_bytes')
  ))
  FROM edition, json_each(edition.verification_json, '$.files') AS j
)
UNION ALL
SELECT 'traversal', json_object(
  'find_occurrence', 'SELECT * FROM asset_occurrences WHERE resource_name LIKE :pattern ORDER BY source_ordinal',
  'resolve_native_table', 'SELECT * FROM asset_native_tables WHERE schema_signature = :schema_signature ORDER BY component',
  'inspect_native_rows', 'SELECT * FROM "<table_name>" LIMIT :n',
  'recover_source', 'Use source_capture and resource_name from asset_occurrences to reopen the standalone file or archive member'
)
UNION ALL
SELECT 'limitations', json_array(
  'raw specialist artifacts are excluded as untrusted',
  'embedded occurrences are traceable to containers, not independently recoverable',
  'CSV values and XLSX cell values are materialized; richer XLSX package structures and PDF, DOCX, image, and IES values remain source-only',
  'no FTS, vectors, generic chunks, or tree relations'
);
