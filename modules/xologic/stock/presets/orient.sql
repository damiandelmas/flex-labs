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
UNION ALL SELECT 'links',count(*) FROM product_links
UNION ALL SELECT 'resources',count(*) FROM product_resources;
-- @query: query_surface
SELECT m.type AS kind,m.name,GROUP_CONCAT(p.name,', ') AS columns
FROM sqlite_master m JOIN pragma_table_info(m.name) p
WHERE m.name IN ('products','xologic_source_observations','xologic_content_objects',
 'source_properties','spec_occurrences','taxonomy_paths','product_links','product_resources',
 'preferred_spec_occurrences','spec_conflicts','resolved_products')
GROUP BY m.type,m.name ORDER BY m.name;
-- @query: limitations
SELECT 'Source strings are preserved as observed; derived preferences are SQL interpretations.' AS limitation
UNION ALL SELECT 'Text FTS, text embeddings, image embeddings, generic relationships, trees, and operation logs are not installed.';
-- @query: examples
SELECT 'products in an exact category and subcategory' purpose,
 'SELECT p.title,p.url FROM products p JOIN taxonomy_paths c ON c.product_ref=p.product_ref AND c.depth=1 JOIN taxonomy_paths s ON s.product_ref=p.product_ref AND s.depth=2 WHERE c.label=:category AND s.label=:subcategory;' sql
UNION ALL SELECT 'products with an exact published specification',
 'SELECT p.title,p.url,o.label,o.value FROM products p JOIN spec_occurrences o USING(product_ref) WHERE o.label=:label AND o.value=:value;'
UNION ALL SELECT 'inspect all source values before preference',
 'SELECT source_layer,source_order,label,value,locator FROM spec_occurrences WHERE product_ref=:product_ref AND lower(label)=lower(:label) ORDER BY source_order;';
