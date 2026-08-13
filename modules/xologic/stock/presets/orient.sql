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
UNION ALL SELECT 'content objects',count(*) FROM xologic_content_objects
UNION ALL SELECT 'spec occurrences',count(*) FROM spec_occurrences
UNION ALL SELECT 'taxonomy paths',count(*) FROM taxonomy_paths
UNION ALL SELECT 'product identifiers',count(*) FROM product_identifiers
UNION ALL SELECT 'product search documents',count(*) FROM product_search_documents
UNION ALL SELECT 'product dossier payloads',count(*) FROM product_dossier_payloads
UNION ALL SELECT 'links',count(*) FROM product_links
UNION ALL SELECT 'resources',count(*) FROM product_resources;
-- @query: query_surface
SELECT m.type AS kind,m.name,GROUP_CONCAT(p.name,', ') AS columns
FROM sqlite_master m JOIN pragma_table_info(m.name) p
WHERE m.name IN ('products','xologic_source_observations','xologic_content_objects',
 'source_properties','spec_occurrences','taxonomy_paths','product_links','product_resources',
 'preferred_spec_occurrences','spec_conflicts','spec_field_map','resolved_spec_values',
 'resolved_products','product_dossiers','product_dossier_payloads',
 'product_identifiers','product_search_documents','products_fts')
GROUP BY m.type,m.name ORDER BY m.name;
-- @query: limitations
SELECT 'Source strings are preserved as observed; derived preferences are SQL interpretations.' AS limitation
UNION ALL SELECT 'products_fts is a rebuildable product-discovery index; native and resolved relations remain the authority for product claims.'
UNION ALL SELECT 'Text embeddings, image embeddings, generic relationships, trees, and operation logs are not installed.';
-- @query: examples
SELECT 'find and completely expand products by title, SKU, part number, vendor, taxonomy, or specifications' purpose,
 'WITH discovered AS (SELECT d.product_ref,bm25(products_fts,0.0,8.0,12.0,4.0,5.0,2.0,1.0) text_rank FROM products_fts JOIN product_search_documents d ON d.rowid=products_fts.rowid WHERE products_fts MATCH :terms), ranked AS (SELECT x.*,row_number() OVER (ORDER BY x.text_rank,x.product_ref) batch_rank FROM discovered x), sized AS (SELECT x.*,sum(length(payload.dossier_json)) OVER (ORDER BY x.batch_rank) cumulative_chars FROM ranked x JOIN product_dossier_payloads payload USING(product_ref)) SELECT dossier.* FROM sized x JOIN product_dossiers dossier USING(product_ref) WHERE x.batch_rank<=5 AND (x.batch_rank=1 OR x.cumulative_chars<=30000) ORDER BY x.text_rank,dossier.title;' sql
UNION ALL SELECT 'find an exact or partial product identifier',
 'WITH discovered AS (SELECT DISTINCT product_ref FROM product_identifiers WHERE normalized_identifier LIKE ''%''||lower(trim(:identifier))||''%''), ranked AS (SELECT x.*,row_number() OVER (ORDER BY x.product_ref) batch_rank FROM discovered x), sized AS (SELECT x.*,sum(length(payload.dossier_json)) OVER (ORDER BY x.batch_rank) cumulative_chars FROM ranked x JOIN product_dossier_payloads payload USING(product_ref)) SELECT dossier.* FROM sized x JOIN product_dossiers dossier USING(product_ref) WHERE x.batch_rank<=5 AND (x.batch_rank=1 OR x.cumulative_chars<=30000) ORDER BY dossier.title,dossier.sku; -- pass only the identifier token, such as 43999 or P400108-31M; do not combine it with title or vendor words'
UNION ALL SELECT 'apply every mandatory requirement after broad product discovery',
 'WITH discovered AS (SELECT d.product_ref,bm25(products_fts,0.0,8.0,12.0,4.0,5.0,2.0,1.0) text_rank FROM products_fts JOIN product_search_documents d ON d.rowid=products_fts.rowid WHERE products_fts MATCH :terms), eligible AS (SELECT p.product_ref,x.text_rank,p.price_min,p.title FROM discovered x JOIN resolved_products p USING(product_ref) WHERE p.price_min<=:max_price AND lower(COALESCE(p.finish,'')) LIKE ''%''||lower(:required_finish)||''%'' AND lower(COALESCE(p.style,'')) LIKE ''%''||lower(:required_style)||''%'' AND EXISTS (SELECT 1 FROM taxonomy_paths t WHERE t.product_ref=p.product_ref AND lower(t.label) LIKE lower(:required_type)||''%'') AND NOT EXISTS (SELECT 1 FROM taxonomy_paths t WHERE t.product_ref=p.product_ref AND (lower(t.label) LIKE ''%accessor%'' OR lower(t.label) LIKE ''%replacement%'' OR lower(t.label) LIKE ''%part%''))) SELECT dossier.* FROM eligible e JOIN product_dossiers dossier USING(product_ref) ORDER BY e.text_rank,e.price_min,e.title;'
UNION ALL SELECT 'rank an optional preference without weakening mandatory requirements',
 'WITH eligible AS (SELECT p.product_ref,p.price_min,p.title,p.style FROM resolved_products p WHERE p.price_min<=:max_price AND lower(COALESCE(p.finish,'')) LIKE ''%''||lower(:required_finish)||''%'' AND EXISTS (SELECT 1 FROM taxonomy_paths t WHERE t.product_ref=p.product_ref AND lower(t.label) LIKE lower(:required_type)||''%'')) SELECT dossier.* FROM eligible e JOIN product_dossiers dossier USING(product_ref) ORDER BY CASE WHEN lower(COALESCE(e.style,'')) LIKE ''%''||lower(:preferred_style)||''%'' THEN 0 ELSE 1 END,e.price_min,e.title;'
UNION ALL SELECT 'products with an exact published specification',
 'WITH eligible AS (SELECT DISTINCT product_ref FROM spec_occurrences WHERE lower(label)=lower(:label) AND lower(value)=lower(:value)) SELECT dossier.* FROM eligible JOIN product_dossiers dossier USING(product_ref) ORDER BY dossier.title;'
UNION ALL SELECT 'inspect all source values before preference',
 'SELECT source_layer,source_order,label,value,locator FROM spec_occurrences WHERE product_ref=:product_ref AND lower(label)=lower(:label) ORDER BY source_order;';
