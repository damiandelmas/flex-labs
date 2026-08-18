-- @name: orient
-- @description: XOLogic native catalogue query contract
-- @multi: true
-- @query: identity
SELECT
  max(CASE WHEN key='description' THEN value END) AS description,
  max(CASE WHEN key='provider' THEN value END) AS provider,
  max(CASE WHEN key='profile' THEN value END) AS profile,
  max(CASE WHEN key='schema_version' THEN value END) AS schema_version,
  max(CASE WHEN key='authority' THEN value END) AS authority,
  max(CASE WHEN key='lifecycle' THEN value END) AS lifecycle
FROM _metadata;
-- @query: capabilities
SELECT json_extract(value,'$.native_products') AS native_products,
       json_extract(value,'$.source_recovery') AS source_recovery,
       json_extract(value,'$.taxonomy') AS taxonomy,
       json_extract(value,'$.product_links') AS product_links,
       json_extract(value,'$.product_resources') AS product_resources,
       json_extract(value,'$.text_retrieval') AS text_retrieval,
       json_extract(value,'$.text_embeddings') AS text_embeddings,
       json_extract(value,'$.image_embeddings') AS image_embeddings,
       json_extract(value,'$.relationship_interop') AS relationship_interop,
       json_extract(value,'$.tree_navigation') AS tree_navigation,
       json_extract(value,'$.operations_log') AS operations_log
FROM _metadata WHERE key='capabilities';
-- @query: edition
SELECT edition_id,store_name,product_count,source_observation_count,products_sha256
FROM xologic_editions;
-- @query: shape
SELECT 'products' what,count(*) n FROM products
UNION ALL SELECT 'source observations',count(*) FROM xologic_source_observations
UNION ALL SELECT 'spec occurrences',count(*) FROM spec_occurrences
UNION ALL SELECT 'taxonomy paths',count(*) FROM taxonomy_paths
UNION ALL SELECT 'product identifiers',count(*) FROM product_identifiers
UNION ALL SELECT 'product search documents',count(*) FROM product_search_documents
UNION ALL SELECT 'product dossier payloads',count(*) FROM product_dossier_payloads
UNION ALL SELECT 'links',count(*) FROM product_links
UNION ALL SELECT 'resources',count(*) FROM product_resources;
-- @query: query_surface
WITH contract(name,grain,contents,join_key) AS (VALUES
 ('products','one row per compiled product identity','Published product identity and compatibility projection','product_ref'),
 ('xologic_source_observations','one row per exact requested source identity','Fetch identity, response identity, parser identity, and the retained capture body pointer','source_id; content_hash'),
 ('source_properties','one row per structured property occurrence','Provider-authored structured property tokens and values','source_id; content_hash'),
 ('spec_occurrences','one row per source occurrence','Exact labels, values, sections, order, and visibility','product_ref; source_id; occurrence_id'),
 ('product_specification','one row per Product Specification occurrence','The provider Product Specification surface without normalization','product_ref; source_id; occurrence_id'),
 ('additional_product_details','one row per Additional product details occurrence','The standardData and additionalData surfaces with their source layer intact','product_ref; source_id; occurrence_id'),
 ('other_details','one row per Other Details occurrence','The provider Other Details surface with exact order and source text','product_ref; source_id; occurrence_id'),
 ('product_links','one row per observed source link relation','Exact target, label, section, order, and relation identity','product_ref; source_id; link_id'),
 ('images','one row per observed image link','Visible image URLs and their source-link evidence','product_ref; source_id; relation_id'),
 ('downloads','one row per observed downloadable document link','Visible download labels, URLs, sections, and order','product_ref; source_id; relation_id'),
 ('options','one row per observed option relation','Provider option targets with exact fragments, labels, sections, and order','product_ref; source_id; relation_id'),
 ('additional_options_that_would_change_other_selections','one row per observed relation in that provider section','The source section named Additional options that would change other selections','product_ref; source_id; relation_id'),
 ('collection_products','one row per observed More In This Collection link','The provider collection-product surface','product_ref; source_id; relation_id'),
 ('related_products','one row per observed Related Products link','The provider related-product surface','product_ref; source_id; relation_id'),
 ('product_resources','one row per product-source resource identity','Deduplicated discover-only image and document inventory','product_ref; source_id; asset_id'),
 ('taxonomy_paths','one row per product taxonomy depth and label','Published product taxonomy paths','product_ref'),
 ('preferred_spec_occurrences','one row per product and preferred label','SQL-selected occurrence precedence over the complete source occurrence relation','product_ref; occurrence_id'),
 ('spec_conflicts','one row per product label with conflicting values','Distinct observed values that disagree within one product','product_ref'),
 ('spec_field_map','one row per exact normalized source label','Declared mapping from source labels to derived lighting fields','normalized_label'),
 ('resolved_spec_values','one row per mapped source occurrence','Typed SQL interpretation with parse status and provenance','product_ref; occurrence_id'),
 ('resolved_products','one row per compiled product identity','Derived buyer-facing fields beside the source-faithful relations','product_ref'),
 ('product_identifiers','one row per product identifier occurrence','Searchable SKU, part number, GTIN, and handle identities','product_ref'),
 ('product_search_documents','one row per compiled product identity','Rebuildable product-discovery text projection','product_ref'),
 ('products_fts','one row per compiled product identity','FTS5 index over product_search_documents','product_ref'),
 ('product_dossiers','one row per compiled product identity','Complete derived product dossier with native relations represented as JSON arrays','product_ref'),
 ('product_dossier_payloads','one row per compiled product identity','Deterministically serialized complete dossier','product_ref')
)
SELECT m.type AS kind,c.name,c.grain,c.contents,c.join_key,
       GROUP_CONCAT(p.name,', ') AS columns
FROM contract c
JOIN sqlite_master m ON m.name=c.name
JOIN pragma_table_info(c.name) p
GROUP BY m.type,c.name,c.grain,c.contents,c.join_key
ORDER BY c.name;
-- @query: limitations
SELECT 'Source strings are preserved as observed; derived preferences are SQL interpretations.' AS limitation
UNION ALL SELECT 'products_fts is a rebuildable product-discovery index; native and resolved relations remain the authority for product claims.'
UNION ALL SELECT 'Image and document links are retained as visible source relations; their target bytes are not part of this edition.'
UNION ALL SELECT 'Text embeddings, image embeddings, generic relationships, trees, and operation logs are not installed.';
